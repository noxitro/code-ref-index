// 段階 1 の索引生成: compile_commands.json の TU を libtooling の clang::index で走査し、
// プロジェクト内の参照を TSV に出す。
//   記号 <TAB> 役割 <TAB> 呼び出し元 <TAB> path:line:col <TAB> ソース行
// 役割 dep は、テンプレート内で解決先が決まらない呼び出し・参照(記号は ?::名前)。
// 役割の接尾辞 -using は、テンプレート内の using 宣言越しの参照を、実体化から引いた宣言の先で出したもの。
// TU ごとの成否は別ファイル(<out>.tu.tsv)に出す。
// コメントと無効な #if の範囲も別ファイル(<out>.skip.tsv)に出す。テキスト検索の一致が
// 索引に無い理由を、問い合わせ側(ltq)が答えるのに使う。
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
#include "llvm/Support/CommandLine.h"
#include "llvm/Support/FileSystem.h"
#include "llvm/Support/Path.h"
#include "llvm/Support/raw_ostream.h"

#include <atomic>
#include <chrono>
#include <mutex>
#include <set>
#include <string>
#include <thread>
#include <vector>

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
static cl::list<std::string> Files(cl::Positional, cl::desc("[対象ファイル...](省略時は全 TU)"));

namespace {

std::string normalize(llvm::StringRef P) {
  llvm::SmallString<260> S(P);
  llvm::sys::fs::make_absolute(S);
  llvm::sys::path::remove_dots(S, true);
  llvm::sys::path::native(S, llvm::sys::path::Style::windows_slash);
  return std::string(S);
}

bool underRoot(const std::string &Abs, const std::string &R, std::string &Rel) {
  if (Abs.size() <= R.size() || !llvm::StringRef(Abs).starts_with_insensitive(R)) return false;
  Rel = Abs.substr(R.size());
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

// ルート配下の位置だけを TSV の行にする。索引と依存呼び出しの両方が使う。
class RowWriter {
public:
  RowWriter(const std::string &R, std::vector<std::string> &Rows, std::vector<std::string> &Skips)
      : Root(R), Rows(Rows), Skips(Skips) {}

  void add(const SourceManager &SM, SourceLocation Loc, std::string Sym, const std::string &Role,
           std::string Caller) {
    SourceLocation FL = SM.getFileLoc(Loc);
    FileID FID = SM.getFileID(FL);
    const std::string &Path = relPath(SM, FID);
    if (Path.empty() || Sym.empty()) return;

    unsigned Line = SM.getSpellingLineNumber(FL), Col = SM.getSpellingColumnNumber(FL);
    llvm::StringRef Buf = SM.getBufferData(FID);
    unsigned Off = SM.getFileOffset(FL);
    size_t B = Buf.rfind('\n', Off == 0 ? 0 : Off - 1);
    B = (B == llvm::StringRef::npos || Off == 0) ? 0 : B + 1;
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
      It = PathCache.try_emplace(FID, FE && underRoot(normalize(FE->getName()), Root, Rel) ? Rel : "").first;
    }
    return It->second;
  }

  std::string Root;
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

class IndexAction : public ASTFrontendAction {
public:
  IndexAction(const std::string &R, std::vector<std::string> &Rows, std::vector<std::string> &Skips)
      : W(R, Rows, Skips), C(W) {}

  bool BeginSourceFileAction(CompilerInstance &CI) override {
    Preprocessor &PP = CI.getPreprocessor();
    PP.addPPCallbacks(std::make_unique<SkippedRanges>(CI.getSourceManager(), W));
    PP.addCommentHandler(&C);
    return true;
  }
  void EndSourceFileAction() override { getCompilerInstance().getPreprocessor().removeCommentHandler(&C); }

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
  RowWriter W;
  Comments C;
};

class Factory : public tooling::FrontendActionFactory {
public:
  Factory(const std::string &R, std::vector<std::string> &Rows, std::vector<std::string> &Skips)
      : Root(R), Rows(Rows), Skips(Skips) {}
  std::unique_ptr<FrontendAction> create() override { return std::make_unique<IndexAction>(Root, Rows, Skips); }

private:
  std::string Root;
  std::vector<std::string> &Rows, &Skips;
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

}  // namespace

int main(int argc, const char **argv) {
  cl::ParseCommandLineOptions(argc, argv, "libtooling 版の C++ 参照索引(段階 1)\n");
  std::string Err;
  auto Cdb = tooling::CompilationDatabase::loadFromDirectory(CdbPath, Err);
  if (!Cdb) { llvm::errs() << Err << "\n"; return 2; }

  std::vector<std::string> Todo(Files.begin(), Files.end());
  if (Todo.empty())
    for (const auto &F : Cdb->getAllFiles())
      if (llvm::sys::fs::exists(F) && !llvm::StringRef(F).ends_with(".ixx")) Todo.push_back(F);
  std::string RootN = normalize(Root);

  std::error_code EC;
  llvm::raw_fd_ostream OutS(Out, EC), TuS(Out + ".tu.tsv", EC), SkipS(Out + ".skip.tsv", EC);
  if (EC) { llvm::errs() << EC.message() << "\n"; return 2; }
  TuS << "file\tok\terrors\tsec\trefs\tfirst_error\n";
  SkipS << "path\tkind\tbegin\tend\n";

  std::mutex M;
  std::set<std::string> Seen, SeenSkip;
  std::atomic<size_t> Next{0}, Ok{0};
  auto Worker = [&] {
    for (size_t I; (I = Next++) < Todo.size();) {
      auto T0 = std::chrono::steady_clock::now();
      std::vector<std::string> Rows, Skips;
      tooling::ClangTool Tool(*Cdb, {Todo[I]});
      Tool.appendArgumentsAdjuster([](const tooling::CommandLineArguments &A, llvm::StringRef) {
        tooling::CommandLineArguments R;
        for (const auto &S : A) {
          llvm::StringRef X(S);
          if (X.size() > 3 && (X[0] == '/' || X[0] == '-') &&
              (X.substr(1).starts_with("Yu") || X.substr(1).starts_with("Yc") || X.substr(1).starts_with("Fp")))
            continue;  // PCH は使わない(大文字小文字を区別して /fp:precise を残す)
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
      Factory F(RootN, Rows, Skips);
      int Rc = Tool.run(&F);
      double Sec = std::chrono::duration<double>(std::chrono::steady_clock::now() - T0).count();
      bool Good = Rc == 0 && Diag.Errors == 0;
      if (Good) ++Ok;

      std::lock_guard<std::mutex> L(M);
      size_t New = 0;
      for (auto &R : Rows)
        if (Seen.insert(R).second) { OutS << R << "\n"; ++New; }
      for (auto &S : Skips)
        if (SeenSkip.insert(S).second) SkipS << S << "\n";
      TuS << Todo[I] << "\t" << (Good ? 1 : 0) << "\t" << Diag.Errors << "\t"
          << llvm::format("%.2f", Sec) << "\t" << New << "\t" << Diag.First << "\n";
      llvm::errs() << "[" << (I + 1) << "/" << Todo.size() << "] " << (Good ? "ok " : "NG ")
                   << llvm::sys::path::filename(Todo[I]) << "\n";
    }
  };
  std::vector<std::thread> Ts;
  for (unsigned J = 0; J < std::max(1u, (unsigned)Jobs); ++J) Ts.emplace_back(Worker);
  for (auto &T : Ts) T.join();
  llvm::errs() << "TU " << Ok << "/" << Todo.size() << " ok, rows " << Seen.size() << "\n";
  return Ok == Todo.size() ? 0 : 1;
}
