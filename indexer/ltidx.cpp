// 段階 1 の索引生成: compile_commands.json の TU を libtooling の clang::index で走査し、
// プロジェクト内の参照を TSV に出す。
//   記号 <TAB> 役割 <TAB> 呼び出し元 <TAB> path:line:col <TAB> ソース行
// 役割 dep は、テンプレート内で解決先が決まらない呼び出し・参照(記号は ?::名前)。
// 役割の接尾辞 -using は、テンプレート内の using 宣言越しの参照を、実体化から引いた宣言の先で出したもの。
// 役割 override は仮想関数の上書き。3 列目は呼び出し元ではなく、上書きされる基底のメソッド。
// TU ごとの成否は別ファイル(<out>.tu.tsv)に、TU が読んだルート配下のファイルは <out>.deps.tsv に出す。
// コメントと無効な #if の範囲も別ファイル(<out>.skip.tsv)に出す。テキスト検索の一致が
// 索引に無い理由を、問い合わせ側(ltq)が答えるのに使う。
// -update は、前回の索引の後に変わったファイルを読む TU・コマンドの変わった TU・新しい TU だけを解析し直して差し替える。
#include "clang/AST/ASTContext.h"
#include "clang/AST/Decl.h"
#include "clang/AST/ExprCXX.h"
#include "clang/AST/RecursiveASTVisitor.h"
#include "clang/Basic/SourceManager.h"
#include "clang/Frontend/CompilerInstance.h"
#include "clang/Frontend/FrontendAction.h"
#include "clang/Frontend/MultiplexConsumer.h"
#include "clang/Index/IndexDataConsumer.h"
#include "clang/Index/IndexingAction.h"
#include "clang/Index/IndexSymbol.h"
#include "clang/Lex/PPCallbacks.h"
#include "clang/Lex/Preprocessor.h"
#include "clang/Tooling/ArgumentsAdjusters.h"
#include "clang/Tooling/CompilationDatabase.h"
#include "clang/Tooling/Tooling.h"
#include "llvm/ADT/StringExtras.h"
#include "llvm/Support/CommandLine.h"
#include "llvm/Support/ConvertUTF.h"
#include "llvm/Support/FileSystem.h"
#include "llvm/Support/MemoryBuffer.h"
#include "llvm/Support/Path.h"
#include "llvm/Support/VirtualFileSystem.h"
#include "llvm/Support/raw_ostream.h"
#include "llvm/Support/xxhash.h"

#include <algorithm>
#include <atomic>
#include <chrono>
#include <map>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>

bool codepageToUtf8(unsigned Cp, const char *P, size_t N, std::string &Out);  // codepage.cpp
unsigned ansiCodepage();
std::string shortPath(const std::string &P);  // 8.3 形式の短い名前(取れなければそのまま)

using namespace clang;
namespace cl = llvm::cl;

static cl::opt<std::string> CdbPath("p", cl::desc("compile_commands.json のあるディレクトリ"), cl::Required);
static cl::opt<std::string> Root("root", cl::desc("索引に含めるソースのルート(これ以外の場所の参照は捨てる)"), cl::Required);
static cl::opt<std::string> Out("o", cl::desc("出力 TSV"), cl::Required);
static cl::opt<std::string> ResourceDir("resource-dir", cl::desc("clang の組み込みヘッダーの場所"), cl::init(LTIDX_RESOURCE_DIR));
static cl::opt<unsigned> Jobs("j", cl::desc("並列数"), cl::init(4));
static cl::opt<bool> NoInst("no-inst", cl::desc("テンプレートの暗黙の実体化を索引しない(比較用)"));
static cl::opt<bool> Delayed("delayed", cl::desc("clang-cl 既定の遅延テンプレート解析のままにする(/Zc:twoPhase を足さない)"));
static cl::opt<bool> NoDep("no-dep", cl::desc("テンプレート内の解決できない呼び出し(dep 行)を出さない"));
static cl::opt<bool> Update("update", cl::desc("前回の索引の後に変わった TU だけを解析し直して差し替える"));
static cl::opt<unsigned> SourceCp("source-cp", cl::desc("UTF-8 でないソースを読むコードページ(既定はこの PC の ANSI コードページ。"
                                                        "TU の /source-charset が優先)"), cl::init(0));
static cl::list<std::string> Files(cl::Positional, cl::desc("[対象ファイル...](省略時は全 TU)"));

namespace {

// 相対パスは、TU のコンパイルのディレクトリ(FileManager の作業ディレクトリ)から解決する
std::string normalize(llvm::StringRef P, const FileManager *FM = nullptr) {
  llvm::SmallString<260> S(P);
  if (FM)
    FM->makeAbsolutePath(S);
  else
    llvm::sys::fs::make_absolute(S);
  llvm::sys::path::remove_dots(S, true);
  llvm::sys::path::native(S, llvm::sys::path::Style::windows_slash);
  return std::string(S);
}

// Roots はルートの別の書き方(渡されたまま・長い名前・8.3 形式の短い名前)。どれかで始まればルート配下
bool underRoot(const std::string &Abs, const std::vector<std::string> &Roots, std::string &Rel) {
  const std::string *Hit = nullptr;
  for (const auto &R : Roots)
    if (Abs.size() > R.size() && llvm::StringRef(Abs).starts_with_insensitive(R) &&
        (Abs[R.size()] == '\\' || Abs[R.size()] == '/' || R.back() == '\\' || R.back() == '/')) {
      Hit = &R;
      break;
    }
  if (!Hit) return false;
  Rel = Abs.substr(Hit->size());
  while (!Rel.empty() && (Rel[0] == '\\' || Rel[0] == '/')) Rel.erase(0, 1);
  for (char &C : Rel) if (C == '\\') C = '/';
  return true;
}

std::string roleOf(index::SymbolRoleSet R) {
  using index::SymbolRole;
  std::string S = (R & (unsigned)SymbolRole::Definition)    ? "def"
                  : (R & (unsigned)SymbolRole::Declaration) ? "decl"
                  : (R & (unsigned)SymbolRole::Call)        ? "call"
                  : (R & (unsigned)SymbolRole::Write)       ? "write"
                                                             : "ref";
  if (R & (unsigned)SymbolRole::Implicit) S += "-implicit";
  return S;
}

// テンプレート引数を含まない修飾名。実体化ごとに別名(Foo<int>::f, Foo<char>::f)にすると、
// 同じ位置の行が実体化の数だけ増えるので、Foo::f にまとめる。
std::string partName(const NamedDecl *ND) {
  if (const auto *R = dyn_cast<CXXRecordDecl>(ND); R && R->isLambda()) return "(lambda)";
  if (isa<CXXConstructorDecl>(ND) || isa<CXXDestructorDecl>(ND)) {
    std::string P = cast<NamedDecl>(cast<Decl>(ND->getDeclContext()))->getName().str();
    return isa<CXXDestructorDecl>(ND) ? "~" + P : P;
  }
  std::string S = ND->getDeclName().getAsString();
  return S.empty() ? "(anonymous)" : S;
}

std::string qname(const Decl *D) {
  const auto *ND = dyn_cast_or_null<NamedDecl>(D);
  if (!ND) return "";
  std::string S = partName(ND);
  for (const DeclContext *C = ND->getDeclContext(); C && !C->isTranslationUnit(); C = C->getParent())
    if (const auto *P = dyn_cast<NamedDecl>(C)) {
      if (const auto *NS = dyn_cast<NamespaceDecl>(P); NS && NS->isInline()) continue;
      S = partName(P) + "::" + S;
    }
  return S;
}

void sanitize(std::string &S) {
  for (char &C : S) if (C == '\t' || C == '\r' || C == '\n') C = ' ';
}

// cl は BOM の無いソースをコードページ(既定は ANSI コードページ。日本語版 Windows なら 932 = Shift_JIS)で読むが、
// clang は UTF-8 として読む。Shift_JIS の 2 バイト目の `\` が行の継続や文字列のエスケープになり、
// 次の行がコメントに飲まれたり TU が失敗したりする。UTF-8 として正しくないソースと UTF-16 のソースは、
// 読む前に UTF-8 に直して clang に渡す。列番号は UTF-8 に直した行のバイト位置になる(ltq も同じ変換で突き合わせる)。
class Transcoder {
public:
  // 変換した内容。変換の要らないファイルは null。ファイルごとに 1 回だけ読む(全スレッドで共有)
  std::shared_ptr<const std::string> get(llvm::vfs::FileSystem &FS, const llvm::Twine &Path, unsigned Cp) {
    llvm::SmallString<260> Abs;
    Path.toVector(Abs);
    FS.makeAbsolute(Abs);
    std::string Key = std::to_string(Cp) + "|" + Abs.str().str();
    {
      std::lock_guard<std::mutex> L(M);
      if (auto It = Cache.find(Key); It != Cache.end()) return It->second;
    }
    std::shared_ptr<const std::string> R;
    if (auto Buf = FS.getBufferForFile(Abs, -1, false)) R = convert((*Buf)->getBuffer(), Cp);
    std::lock_guard<std::mutex> L(M);
    return Cache.try_emplace(Key, R).first->second;
  }

private:
  static std::shared_ptr<const std::string> convert(llvm::StringRef B, unsigned Cp) {
    std::string Out;
    llvm::ArrayRef<char> Bytes(B.data(), B.size());
    if (llvm::hasUTF16ByteOrderMark(Bytes))
      return llvm::convertUTF16ToUTF8String(Bytes, Out) ? std::make_shared<const std::string>(std::move(Out)) : nullptr;
    if (Cp == 65001 || B.starts_with("\xEF\xBB\xBF")) return nullptr;
    const auto *P = reinterpret_cast<const llvm::UTF8 *>(B.begin());
    if (llvm::isLegalUTF8String(&P, reinterpret_cast<const llvm::UTF8 *>(B.end()))) return nullptr;
    return codepageToUtf8(Cp, B.data(), B.size(), Out) ? std::make_shared<const std::string>(std::move(Out)) : nullptr;
  }

  std::mutex M;
  std::map<std::string, std::shared_ptr<const std::string>> Cache;
};

class TranscodedFile : public llvm::vfs::File {
public:
  TranscodedFile(llvm::vfs::Status S, std::shared_ptr<const std::string> D) : S(std::move(S)), D(std::move(D)) {}
  llvm::ErrorOr<llvm::vfs::Status> status() override { return S; }
  llvm::ErrorOr<std::unique_ptr<llvm::MemoryBuffer>> getBuffer(const llvm::Twine &Name, int64_t, bool, bool) override {
    return llvm::MemoryBuffer::getMemBuffer(*D, Name.str());  // D は Transcoder が持ち続ける
  }
  std::error_code close() override { return {}; }

private:
  llvm::vfs::Status S;
  std::shared_ptr<const std::string> D;
};

// 変換したファイルは、大きさも変換後のものを返す(SourceManager が読んだ大きさと突き合わせる)
class TranscodingFS : public llvm::vfs::ProxyFileSystem {
public:
  TranscodingFS(llvm::IntrusiveRefCntPtr<llvm::vfs::FileSystem> Base, unsigned Cp, Transcoder &T)
      : ProxyFileSystem(std::move(Base)), Cp(Cp), T(T) {}

  llvm::ErrorOr<llvm::vfs::Status> status(const llvm::Twine &Path) override {
    auto S = ProxyFileSystem::status(Path);
    if (!S || !S->isRegularFile()) return S;
    if (auto D = T.get(getUnderlyingFS(), Path, Cp)) return llvm::vfs::Status::copyWithNewSize(*S, D->size());
    return S;
  }
  llvm::ErrorOr<std::unique_ptr<llvm::vfs::File>> openFileForRead(const llvm::Twine &Path) override {
    auto F = ProxyFileSystem::openFileForRead(Path);
    if (!F) return F;
    auto D = T.get(getUnderlyingFS(), Path, Cp);
    if (!D) return F;
    auto S = (*F)->status();
    if (!S) return S.getError();
    auto NewS = llvm::vfs::Status::copyWithNewSize(*S, D->size());  // D を渡す前に(引数の評価順は決まっていない)
    return std::unique_ptr<llvm::vfs::File>(new TranscodedFile(std::move(NewS), std::move(D)));
  }

private:
  unsigned Cp;
  Transcoder &T;
};

// /source-charset:<名前> などのコードページ。UTF-8 なら 65001、指定が無い・分からなければ 0
unsigned charsetCodepage(llvm::StringRef V) {
  V = V.trim();
  unsigned N;
  if (V.consume_front(".")) return V.getAsInteger(10, N) ? 0 : N;
  std::string L = V.lower();
  static const std::pair<const char *, unsigned> Names[] = {
      {"utf-8", 65001},     {"utf8", 65001},       {"shift_jis", 932}, {"shift-jis", 932}, {"sjis", 932},
      {"cp932", 932},       {"windows-31j", 932},  {"euc-jp", 51932},  {"gb2312", 936},    {"gbk", 936},
      {"cp936", 936},       {"big5", 950},         {"cp950", 950},     {"euc-kr", 949},    {"ks_c_5601-1987", 949},
      {"cp949", 949},       {"windows-1252", 1252}, {"cp1252", 1252},  {"iso-8859-1", 28591}};
  for (const auto &[Name, Cp] : Names)
    if (L == Name) return Cp;
  return 0;
}

// コンパイルのコマンドからソースのコードページを決める(cl の /utf-8・/source-charset、clang の -finput-charset)
unsigned sourceCodepage(const std::vector<std::string> &Args, unsigned Default) {
  unsigned Cp = Default;
  for (llvm::StringRef A : Args) {
    if (A == "/utf-8" || A == "-utf-8") Cp = 65001;
    else if ((A.consume_front("/source-charset:") || A.consume_front("-source-charset:") ||
              A.consume_front("-finput-charset=")))
      if (unsigned C = charsetCodepage(A)) Cp = C;
  }
  return Cp;
}

bool isCharsetArg(llvm::StringRef A) {
  return A.starts_with("/source-charset:") || A.starts_with("-source-charset:") || A.starts_with("/execution-charset:") ||
         A.starts_with("-execution-charset:") || A.starts_with("-finput-charset=") || A.starts_with("-fexec-charset=");
}

// ルート配下の位置だけを TSV の行にする。索引と依存呼び出しの両方が使う。
class RowWriter {
public:
  RowWriter(const std::vector<std::string> &R, std::vector<std::string> &Rows, std::vector<std::string> &Skips)
      : Roots(R), Rows(Rows), Skips(Skips) {}

  void add(const SourceManager &SM, SourceLocation Loc, std::string Sym, const std::string &Role,
           std::string Caller) {
    SourceLocation FL = SM.getFileLoc(Loc);
    FileID FID = SM.getFileID(FL);
    const std::string &Path = relPath(SM, FID);
    if (Path.empty() || Sym.empty()) return;

    unsigned Line = SM.getSpellingLineNumber(FL), Col = SM.getSpellingColumnNumber(FL);
    llvm::StringRef Buf = SM.getBufferData(FID);
    unsigned Off = SM.getFileOffset(FL);
    size_t B = Buf.rfind('\n', Off);  // [0, Off) を探す。列 1 のトークンでも直前の改行を見つける
    B = B == llvm::StringRef::npos ? 0 : B + 1;
    size_t E = Buf.find('\n', Off);
    std::string Src = Buf.slice(B, E == llvm::StringRef::npos ? Buf.size() : E).trim().str();
    if (Src.size() > 200) Src.resize(200);

    sanitize(Sym);
    sanitize(Caller);
    sanitize(Src);
    Rows.push_back(Sym + "\t" + Role + "\t" + Caller + "\t" + Path + ":" +
                   std::to_string(Line) + ":" + std::to_string(Col) + "\t" + Src);
  }

  // コメント(comment)と無効な #if(inactive)の範囲。path <TAB> 種類 <TAB> line:col <TAB> line:col
  void addRange(const SourceManager &SM, SourceRange R, const char *Kind) {
    SourceLocation B = SM.getFileLoc(R.getBegin()), E = SM.getFileLoc(R.getEnd());
    FileID FID = SM.getFileID(B);
    if (B.isInvalid() || E.isInvalid() || FID != SM.getFileID(E)) return;
    const std::string &Path = relPath(SM, FID);
    if (Path.empty()) return;
    Skips.push_back(Path + "\t" + Kind + "\t" + std::to_string(SM.getSpellingLineNumber(B)) + ":" +
                    std::to_string(SM.getSpellingColumnNumber(B)) + "\t" +
                    std::to_string(SM.getSpellingLineNumber(E)) + ":" +
                    std::to_string(SM.getSpellingColumnNumber(E)));
  }

private:
  // ルート配下ならルートからの相対パス、それ以外は空
  const std::string &relPath(const SourceManager &SM, FileID FID) {
    auto It = PathCache.find(FID);
    if (It == PathCache.end()) {
      std::string Rel;
      OptionalFileEntryRef FE = SM.getFileEntryRefForID(FID);
      It = PathCache.try_emplace(FID, FE && underRoot(normalize(FE->getName(), &SM.getFileManager()), Roots, Rel) ? Rel : "").first;
    }
    return It->second;
  }

  std::vector<std::string> Roots;
  std::vector<std::string> &Rows, &Skips;
  llvm::DenseMap<FileID, std::string> PathCache;
};

class SkippedRanges : public PPCallbacks {
public:
  SkippedRanges(const SourceManager &SM, RowWriter &W) : SM(SM), W(W) {}
  void SourceRangeSkipped(SourceRange R, SourceLocation) override { W.addRange(SM, R, "inactive"); }

private:
  const SourceManager &SM;
  RowWriter &W;
};

class Comments : public CommentHandler {
public:
  explicit Comments(RowWriter &W) : W(W) {}
  bool HandleComment(Preprocessor &PP, SourceRange R) override {
    W.addRange(PP.getSourceManager(), R, "comment");
    return false;
  }

private:
  RowWriter &W;
};

class Consumer : public index::IndexDataConsumer {
public:
  explicit Consumer(RowWriter &W) : W(W) {}
  void initialize(ASTContext &C) override { Ctx = &C; }

  bool handleDeclOccurrence(const Decl *D, index::SymbolRoleSet Roles,
                            llvm::ArrayRef<index::SymbolRelation> Rels, SourceLocation Loc,
                            ASTNodeInfo) override {
    if (isa<NamespaceDecl>(D) || isa<NamespaceAliasDecl>(D)) return true;  // 雑音なので出さない
    std::string Caller;
    for (const auto &Rel : Rels)
      if (Rel.Roles & (unsigned)index::SymbolRole::RelationCalledBy) { Caller = qname(Rel.RelatedSymbol); break; }
    if (Caller.empty())
      for (const auto &Rel : Rels)
        if (Rel.Roles & (unsigned)index::SymbolRole::RelationContainedBy) { Caller = qname(Rel.RelatedSymbol); break; }
    std::string Role = roleOf(Roles);
    W.add(Ctx->getSourceManager(), Loc, qname(D), Role, Caller);
    // 仮想関数の上書き。基底のポインタ越しの呼び出しは基底のメソッドに解決されるので、ltq が上書きの関係から辿る
    for (const auto &Rel : Rels)
      if (Rel.Roles & (unsigned)index::SymbolRole::RelationOverrideOf)
        W.add(Ctx->getSourceManager(), Loc, qname(D), "override", qname(Rel.RelatedSymbol));
    // テンプレート内の `using Base::f;` 越しの参照は、宣言の先が実体化でしか分からないので TU の終わりに引く
    if (const auto *U = dyn_cast<UnresolvedUsingValueDecl>(D); U && !(Roles & (unsigned)index::SymbolRole::Declaration))
      Pending.push_back({U, Loc, Role, Caller});
    return true;
  }

  struct Using {
    const UnresolvedUsingValueDecl *U;
    SourceLocation Loc;
    std::string Role, Caller;
  };
  std::vector<Using> Pending;

private:
  RowWriter &W;
  ASTContext *Ctx = nullptr;
};

// `using Base::f;` を持つクラステンプレートの実体化を見て、f の宣言の先(UsingShadowDecl の先)を集める。
// 実体化が TU に無い・部分特殊化の中にある場合は空になり、元の行(I::f)だけが残る。
std::vector<std::string> usingTargets(const UnresolvedUsingValueDecl *U) {
  std::set<std::string> R;
  const auto *RD = dyn_cast<CXXRecordDecl>(U->getDeclContext());
  const ClassTemplateDecl *CT = RD ? RD->getDescribedClassTemplate() : nullptr;
  if (!CT) return {};
  for (const ClassTemplateSpecializationDecl *S : CT->specializations()) {
    if (!S->isCompleteDefinition()) continue;
    for (const NamedDecl *N : S->lookup(U->getDeclName()))
      if (const auto *Sh = dyn_cast<UsingShadowDecl>(N)) {
        const NamedDecl *T = Sh->getTargetDecl();
        if (const auto *FT = dyn_cast<FunctionTemplateDecl>(T)) T = FT->getTemplatedDecl();
        R.insert(qname(T));
      }
  }
  return {R.begin(), R.end()};
}

class UsingResolver : public ASTConsumer {
public:
  UsingResolver(std::shared_ptr<Consumer> C, RowWriter &W) : C(std::move(C)), W(W) {}
  void HandleTranslationUnit(ASTContext &Ctx) override {
    llvm::DenseMap<const Decl *, std::vector<std::string>> Cache;
    for (const auto &P : C->Pending) {
      auto It = Cache.find(P.U);
      if (It == Cache.end()) It = Cache.try_emplace(P.U, usingTargets(P.U)).first;
      for (const auto &T : It->second) W.add(Ctx.getSourceManager(), P.Loc, T, P.Role + "-using", P.Caller);
    }
  }

private:
  std::shared_ptr<Consumer> C;
  RowWriter &W;
};

// テンプレートの中で解決先が決まらない呼び出し・参照(t.f(), this->f(), Base::f(), f(t) の ADL)。
// 実体化されないテンプレートのものは clang::index に現れないので、名前だけの行 `?::f <TAB> dep` にする。
// RecursiveASTVisitor は既定で実体化を辿らないので、見るのはテンプレートの本体だけになる。
class DepVisitor : public RecursiveASTVisitor<DepVisitor> {
public:
  DepVisitor(ASTContext &C, RowWriter &W) : SM(C.getSourceManager()), W(W) {}

  bool TraverseFunctionDecl(FunctionDecl *F) { return withCaller(F, [&] { return RecursiveASTVisitor::TraverseFunctionDecl(F); }); }
  bool TraverseCXXMethodDecl(CXXMethodDecl *F) { return withCaller(F, [&] { return RecursiveASTVisitor::TraverseCXXMethodDecl(F); }); }
  bool TraverseCXXConstructorDecl(CXXConstructorDecl *F) { return withCaller(F, [&] { return RecursiveASTVisitor::TraverseCXXConstructorDecl(F); }); }
  bool TraverseCXXDestructorDecl(CXXDestructorDecl *F) { return withCaller(F, [&] { return RecursiveASTVisitor::TraverseCXXDestructorDecl(F); }); }

  bool VisitCXXDependentScopeMemberExpr(CXXDependentScopeMemberExpr *E) { return emit(E->getMemberLoc(), E->getMember()); }
  bool VisitDependentScopeDeclRefExpr(DependentScopeDeclRefExpr *E) { return emit(E->getLocation(), E->getDeclName()); }
  bool VisitUnresolvedMemberExpr(UnresolvedMemberExpr *E) { return emit(E->getMemberLoc(), E->getMemberName()); }
  bool VisitUnresolvedLookupExpr(UnresolvedLookupExpr *E) { return emit(E->getNameLoc(), E->getName()); }

private:
  template <class Fn> bool withCaller(FunctionDecl *F, Fn &&Body) {
    std::string Saved = Caller;
    Caller = qname(F);
    bool R = Body();
    Caller = Saved;
    return R;
  }
  bool emit(SourceLocation Loc, DeclarationName N) {
    if (Loc.isInvalid() || SM.isInSystemHeader(Loc) || N.isEmpty()) return true;
    W.add(SM, Loc, "?::" + N.getAsString(), "dep", Caller);
    return true;
  }

  const SourceManager &SM;
  RowWriter &W;
  std::string Caller;
};

class DepConsumer : public ASTConsumer {
public:
  explicit DepConsumer(RowWriter &W) : W(W) {}
  void HandleTranslationUnit(ASTContext &C) override { DepVisitor(C, W).TraverseDecl(C.getTranslationUnitDecl()); }

private:
  RowWriter &W;
};

struct Output {
  std::vector<std::string> Rows, Skips, Deps;  // Deps: TU が読んだルート配下のファイル(ルートからの相対パス)
};

class IndexAction : public ASTFrontendAction {
public:
  IndexAction(const std::vector<std::string> &R, Output &O) : Roots(R), O(O), W(R, O.Rows, O.Skips), C(W) {}

  bool BeginSourceFileAction(CompilerInstance &CI) override {
    Preprocessor &PP = CI.getPreprocessor();
    PP.addPPCallbacks(std::make_unique<SkippedRanges>(CI.getSourceManager(), W));
    PP.addCommentHandler(&C);
    return true;
  }
  void EndSourceFileAction() override {
    CompilerInstance &CI = getCompilerInstance();
    CI.getPreprocessor().removeCommentHandler(&C);
    const SourceManager &SM = CI.getSourceManager();
    for (auto It = SM.fileinfo_begin(); It != SM.fileinfo_end(); ++It) {
      std::string Rel;
      if (underRoot(normalize(It->first.getName(), &SM.getFileManager()), Roots, Rel)) O.Deps.push_back(Rel);
    }
  }

  std::unique_ptr<ASTConsumer> CreateASTConsumer(CompilerInstance &CI, llvm::StringRef) override {
    index::IndexingOptions O;
    O.SystemSymbolFilter = index::IndexingOptions::SystemSymbolFilterKind::None;
    O.IndexFunctionLocals = false;
    O.IndexImplicitInstantiation = !NoInst;  // DeferIndexingToEndOfTranslationUnit は指定しない
    std::vector<std::unique_ptr<ASTConsumer>> Cs;
    auto C = std::make_shared<Consumer>(W);
    Cs.push_back(index::createIndexingASTConsumer(C, O, CI.getPreprocessorPtr()));
    Cs.push_back(std::make_unique<UsingResolver>(C, W));  // 索引の後に走る(MultiplexConsumer は順に呼ぶ)
    if (!NoDep) Cs.push_back(std::make_unique<DepConsumer>(W));
    return std::make_unique<MultiplexConsumer>(std::move(Cs));
  }

private:
  std::vector<std::string> Roots;
  Output &O;
  RowWriter W;
  Comments C;
};

class Factory : public tooling::FrontendActionFactory {
public:
  Factory(const std::vector<std::string> &R, Output &O) : Roots(R), O(O) {}
  std::unique_ptr<FrontendAction> create() override { return std::make_unique<IndexAction>(Roots, O); }

private:
  std::vector<std::string> Roots;
  Output &O;
};

class ErrorCounter : public DiagnosticConsumer {
public:
  unsigned Errors = 0, Fatal = 0;
  std::string First;
  void HandleDiagnostic(DiagnosticsEngine::Level L, const Diagnostic &Info) override {
    if (L < DiagnosticsEngine::Error) return;
    ++Errors;
    if (L == DiagnosticsEngine::Fatal) ++Fatal;
    if (First.empty()) {
      llvm::SmallString<200> Msg;
      Info.FormatDiagnostic(Msg);
      First = Msg.str().str();
      sanitize(First);
    }
  }
};

// パスの比較用のキー(Windows では大文字小文字を区別しない)
std::string pathKey(llvm::StringRef P) { return llvm::StringRef(normalize(P)).lower(); }

// TU のコンパイルのコマンド(ディレクトリと引数)と、索引の中身を変える ltidx のオプションのハッシュ。
// -update で、コマンドの変わった TU を見つける。オプションを変えて -update すると、全 TU を解析し直す
std::string commandHash(const tooling::CompilationDatabase &Cdb, const std::string &F) {
  std::string S = pathKey(Root) + '\x1f' + ResourceDir + '\x1f' + std::to_string(SourceCp) + '\x1f' + (NoInst ? "i" : "") +
                  (Delayed ? "d" : "") + (NoDep ? "n" : "") + '\x1e';
  for (const auto &C : Cdb.getCompileCommands(F)) {
    S += C.Directory + '\x1f';
    for (const auto &A : C.CommandLine) S += A + '\x1f';
    S += '\x1e';
  }
  return llvm::utohexstr(llvm::xxh3_64bits(llvm::arrayRefFromStringRef<uint8_t>(S)), true);
}

// 空行を除いた行。SkipHeader なら 1 行目(見出し)も除く
void readLines(const std::string &P, std::vector<std::string> &Out, bool SkipHeader) {
  auto B = llvm::MemoryBuffer::getFile(P);
  if (!B) return;
  llvm::StringRef S = (*B)->getBuffer();
  for (bool First = true; !S.empty(); First = false) {
    auto [L, Rest] = S.split('\n');
    S = Rest;
    if (!(First && SkipHeader) && !L.empty()) Out.push_back(L.str());
  }
}

// 索引の行の 4 列目(path:line:col)のパス
llvm::StringRef rowPath(llvm::StringRef Row) {
  for (int I = 0; I < 3; ++I) Row = Row.split('\t').second;
  return Row.split('\t').first.rsplit(':').first.rsplit(':').first;
}

// 前回の索引のうち、-update で残すもの
struct Kept {
  std::vector<std::string> Rows, Skips, Tus, Deps;
};

// 前回の索引と比べて、解析し直す TU(Todo)と残す行(K)を決める。
// 解析し直すのは、新しい TU・前回失敗した TU・コマンドの変わった TU・前回の後に変わった(消えた)ファイルを読む TU。
// 前回の行を捨てるのは、変わったファイルと、解析し直す TU か消えた TU だけが読んでいたファイルの行。
// それ以外のファイル(残す TU も読むヘッダーなど)は、前回の行を残して新しい行を足す。
bool planUpdate(const tooling::CompilationDatabase &Cdb, const std::vector<std::string> &All, const std::string &RootN,
                std::vector<std::string> &Todo, Kept &K) {
  llvm::sys::fs::file_status St;
  if (llvm::sys::fs::status(Out, St) || !llvm::sys::fs::exists(Out + ".deps.tsv")) {
    llvm::errs() << "-update: 前回の索引(" << Out << " と .deps.tsv)がありません。-update を付けずに作り直してください\n";
    return false;
  }
  auto Prev = St.getLastModificationTime();
  struct OldTu {
    std::string Path, Line;
    bool Ok = false;
    std::string Hash;
    std::vector<std::string> Deps;
  };
  std::map<std::string, OldTu> Old;
  std::vector<std::string> Order, Lines;
  readLines(Out + ".tu.tsv", Lines, true);
  for (const auto &L : Lines) {
    llvm::SmallVector<llvm::StringRef, 8> C;
    llvm::StringRef(L).split(C, '\t');
    if (C.size() < 2) continue;
    std::string Key = pathKey(C[0]);
    if (!Old.count(Key)) Order.push_back(Key);
    Old[Key] = {C[0].str(), L, C[1] == "1", C.size() > 6 ? C[6].str() : "", {}};
  }
  Lines.clear();
  readLines(Out + ".deps.tsv", Lines, true);
  for (const auto &L : Lines) {
    auto [T, F] = llvm::StringRef(L).split('\t');
    if (auto It = Old.find(pathKey(T)); It != Old.end()) It->second.Deps.push_back(F.str());
  }

  auto newer = [&](llvm::StringRef P) {
    llvm::sys::fs::file_status S;
    return llvm::sys::fs::status(P, S) || S.getLastModificationTime() > Prev;
  };
  std::map<std::string, bool> Changed;  // ルートからの相対パス -> 前回の後に変わったか消えた
  for (auto &[Key, T] : Old)
    for (const auto &D : T.Deps)
      if (!Changed.count(D)) {
        llvm::SmallString<260> P(RootN);
        llvm::sys::path::append(P, D);
        Changed[D] = newer(P);
      }
  std::set<std::string> Redo, InCdb;
  for (const auto &F : All) {
    std::string Key = pathKey(F);
    InCdb.insert(Key);
    auto It = Old.find(Key);
    bool R = It == Old.end() || !It->second.Ok || It->second.Hash != commandHash(Cdb, F) || newer(F);
    for (size_t J = 0; !R && J < It->second.Deps.size(); ++J) R = Changed[It->second.Deps[J]];
    if (R) {
      Todo.push_back(F);
      Redo.insert(Key);
    }
  }
  std::map<std::string, bool> OnlyRedone;  // そのファイルを読む TU が、すべて解析し直すか消えたもの
  for (auto &[Key, T] : Old) {
    bool Gone = Redo.count(Key) || !InCdb.count(Key);
    for (const auto &D : T.Deps) {
      auto [It, New] = OnlyRedone.try_emplace(D, Gone);
      if (!New) It->second = It->second && Gone;
    }
  }
  std::set<std::string> Drop;
  size_t NChanged = 0, Removed = 0;
  for (const auto &[D, C] : Changed) {
    NChanged += C;
    if (C || OnlyRedone[D]) Drop.insert(D);
  }
  for (const auto &Key : Order) {
    const OldTu &T = Old[Key];
    if (!InCdb.count(Key)) ++Removed;
    if (Redo.count(Key) || !InCdb.count(Key)) continue;
    K.Tus.push_back(T.Line);
    for (const auto &D : T.Deps) K.Deps.push_back(T.Path + "\t" + D);
  }
  Lines.clear();
  readLines(Out, Lines, false);
  for (auto &L : Lines)
    if (!Drop.count(rowPath(L).str())) K.Rows.push_back(std::move(L));
  Lines.clear();
  readLines(Out + ".skip.tsv", Lines, true);
  for (auto &L : Lines)
    if (!Drop.count(llvm::StringRef(L).split('\t').first.str())) K.Skips.push_back(std::move(L));
  llvm::errs() << "update: " << NChanged << " files changed, " << Todo.size() << "/" << All.size()
               << " TU to re-index, " << Removed << " TU removed\n";
  return true;
}

// <P>.tmp で <P> をすべて差し替える。どれかが差し替えられなければ何も変えない(新旧の混ざった索引を残さない)。
// 開かれているファイル(ltq が読んでいる最中など)は Windows では名前を変えられないので、まず古いほうを .old に
// 退かせる。ここで失敗したら退かせた分を戻す。退かせた後の .tmp -> 本体は、宛先が無いので開かれていても失敗しない
bool replaceAll(const std::string (&Outs)[4]) {
  std::vector<std::string> Moved;
  auto rollback = [&] {
    for (const auto &P : Moved) llvm::sys::fs::rename(P + ".old", P);
  };
  for (const auto &P : Outs) {
    if (!llvm::sys::fs::exists(P)) continue;
    llvm::sys::fs::remove(P + ".old");
    // ltq の読み取りは 1 秒もかからないので、10 秒まで待つ(rename 自身も中で繰り返すので、回数でなく時間で区切る)
    auto Deadline = std::chrono::steady_clock::now() + std::chrono::seconds(10);
    std::error_code E;
    while ((E = llvm::sys::fs::rename(P, P + ".old")) && std::chrono::steady_clock::now() < Deadline)
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
    if (E) {
      llvm::errs() << "cannot replace " << P << " (" << E.message() << "); the previous index is kept, new one is in *.tmp\n";
      rollback();
      return false;
    }
    Moved.push_back(P);
  }
  for (const auto &P : Outs)
    if (std::error_code E = llvm::sys::fs::rename(P + ".tmp", P)) {
      llvm::errs() << "rename " << P << ".tmp: " << E.message() << "\n";
      return false;
    }
  for (const auto &P : Moved) llvm::sys::fs::remove(P + ".old");  // 開かれていれば残るが、次の差し替えで上書きする
  return true;
}

}  // namespace

int main(int argc, const char **argv) {
  cl::ParseCommandLineOptions(argc, argv, "libtooling 版の C++ 参照索引(段階 1)\n");
  llvm::sys::TimePoint<> Started = std::chrono::system_clock::now();
  std::string Err;
  auto Cdb = tooling::CompilationDatabase::loadFromDirectory(CdbPath, Err);
  if (!Cdb) { llvm::errs() << Err << "\n"; return 2; }
  if (Update && !Files.empty()) { llvm::errs() << "-update と対象ファイルは一緒に指定できません\n"; return 2; }

  std::vector<std::string> All;
  for (const auto &F : Cdb->getAllFiles())
    if (llvm::sys::fs::exists(F) && !llvm::StringRef(F).ends_with(".ixx")) All.push_back(F);
  std::string RootN = normalize(Root);
  // ルートとファイルのパスで書き方が違うことがある(CI の一時フォルダ RUNNER~1 と、MSBuild が返す runneradmin など)
  std::vector<std::string> Roots{RootN};
  llvm::SmallString<260> Real;
  if (!llvm::sys::fs::real_path(RootN, Real)) Roots.push_back(normalize(Real));
  Roots.push_back(normalize(shortPath(RootN)));
  std::sort(Roots.begin(), Roots.end());
  Roots.erase(std::unique(Roots.begin(), Roots.end()), Roots.end());
  std::vector<std::string> Todo(Files.begin(), Files.end());
  Kept K;
  if (Update) {
    if (!planUpdate(*Cdb, All, RootN, Todo, K)) return 2;
  } else if (Todo.empty()) {
    Todo = All;
  }
  unsigned DefaultCp = SourceCp ? (unsigned)SourceCp : ansiCodepage();
  Transcoder Trans;

  // 書き終えてから差し替える(作っている途中の索引を ltq が読まないように)
  const std::string Outs[] = {Out, Out + ".tu.tsv", Out + ".skip.tsv", Out + ".deps.tsv"};
  std::error_code EC;
  llvm::raw_fd_ostream OutS(Outs[0] + ".tmp", EC), TuS(Outs[1] + ".tmp", EC), SkipS(Outs[2] + ".tmp", EC),
      DepS(Outs[3] + ".tmp", EC);
  if (EC) { llvm::errs() << EC.message() << "\n"; return 2; }
  TuS << "file\tok\terrors\tsec\trefs\tfirst_error\tcommand_hash\tsource_cp\n";
  SkipS << "path\tkind\tbegin\tend\n";
  DepS << "tu\tfile\n";

  std::mutex M;
  std::set<std::string> Seen, SeenSkip;
  for (auto &R : K.Rows)
    if (Seen.insert(R).second) OutS << R << "\n";
  for (auto &S : K.Skips)
    if (SeenSkip.insert(S).second) SkipS << S << "\n";
  for (auto &T : K.Tus) TuS << T << "\n";
  for (auto &D : K.Deps) DepS << D << "\n";
  K = Kept();

  std::atomic<size_t> Next{0}, Ok{0};
  auto Worker = [&] {
    for (size_t I; (I = Next++) < Todo.size();) {
      auto T0 = std::chrono::steady_clock::now();
      Output O;
      auto Cmds = Cdb->getCompileCommands(Todo[I]);
      unsigned Cp = Cmds.empty() ? DefaultCp : sourceCodepage(Cmds[0].CommandLine, DefaultCp);
      // 作業ディレクトリをプロセスと共有しないファイルシステム(TU ごとにコンパイルのディレクトリが違っても並列にできる)
      llvm::IntrusiveRefCntPtr<llvm::vfs::FileSystem> Base(llvm::vfs::createPhysicalFileSystem().release());
      tooling::ClangTool Tool(*Cdb, {Todo[I]}, std::make_shared<PCHContainerOperations>(),
                              llvm::makeIntrusiveRefCnt<TranscodingFS>(Base, Cp, Trans));
      Tool.appendArgumentsAdjuster([](const tooling::CommandLineArguments &A, llvm::StringRef) {
        tooling::CommandLineArguments R;
        for (const auto &S : A) {
          llvm::StringRef X(S);
          if (X.size() > 3 && (X[0] == '/' || X[0] == '-') &&
              (X.substr(1).starts_with("Yu") || X.substr(1).starts_with("Yc") || X.substr(1).starts_with("Fp")))
            continue;  // PCH は使わない(大文字小文字を区別して /fp:precise を残す)
          if (X == "/WX" || X == "-WX" || X == "-Werror" || X.starts_with("-Werror="))
            continue;  // cl に無い clang の警告(override の付け忘れなど)がエラーになり TU が落ちる
          if (isCharsetArg(X))
            continue;  // clang は UTF-8 以外を受け付けない。ソースは TranscodingFS が UTF-8 に直す
          R.push_back(S);
        }
        return R;
      });
      Tool.appendArgumentsAdjuster(tooling::getInsertArgumentAdjuster(
          ("-resource-dir=" + ResourceDir).c_str(), tooling::ArgumentInsertPosition::BEGIN));
      // 遅延解析のままだと、実体化されないテンプレート本体の非依存呼び出しが索引に載らない
      if (!Delayed)
        Tool.appendArgumentsAdjuster(
            tooling::getInsertArgumentAdjuster("/Zc:twoPhase", tooling::ArgumentInsertPosition::END));
      ErrorCounter Diag;
      Tool.setDiagnosticConsumer(&Diag);
      Factory F(Roots, O);
      int Rc = Tool.run(&F);
      double Sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - T0).count();
      bool Good = Rc == 0 && Diag.Errors == 0;
      if (Good) ++Ok;
      std::string Hash = commandHash(*Cdb, Todo[I]);
      std::set<std::string> Deps(O.Deps.begin(), O.Deps.end());

      std::lock_guard<std::mutex> L(M);
      size_t New = 0;
      for (auto &R : O.Rows)
        if (Seen.insert(R).second) { OutS << R << "\n"; ++New; }
      for (auto &S : O.Skips)
        if (SeenSkip.insert(S).second) SkipS << S << "\n";
      for (const auto &D : Deps) DepS << Todo[I] << "\t" << D << "\n";
      TuS << Todo[I] << "\t" << (Good ? 1 : 0) << "\t" << Diag.Errors << "\t" << llvm::format("%.2f", Sec) << "\t"
          << New << "\t" << Diag.First << "\t" << Hash << "\t" << Cp << "\n";
      llvm::errs() << "[" << (I + 1) << "/" << Todo.size() << "] " << (Good ? "ok " : "NG ")
                   << llvm::sys::path::filename(Todo[I]) << "\n";
    }
  };
  std::vector<std::thread> Ts;
  for (unsigned J = 0; J < std::max(1u, (unsigned)Jobs); ++J) Ts.emplace_back(Worker);
  for (auto &T : Ts) T.join();

  for (auto *S : {&OutS, &TuS, &SkipS, &DepS}) {
    S->close();
    if (S->has_error()) { llvm::errs() << "write failed: " << S->error().message() << "\n"; return 2; }
  }
  // 索引の時刻は解析を始めた時刻にする。解析の途中で変わったファイルも、次の -update と ltq が「索引の後に変わった」と扱う
  for (const auto &P : Outs) llvm::sys::fs::setLastAccessAndModificationTime(P + ".tmp", Started);
  if (!replaceAll(Outs)) return 2;
  llvm::errs() << "TU " << Ok << "/" << Todo.size() << " ok, rows " << Seen.size() << "\n";
  return Ok == Todo.size() ? 0 : 1;
}
