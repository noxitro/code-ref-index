# ltidx — C++ の参照索引を作る

## 目的

`compile_commands.json` に載っている TU を clang の libtooling(`clang::index`)で解析し、プロジェクト内の参照を 1 行 1 件の TSV に出します。
問い合わせのたびに解析し直す必要がありません。テンプレートの実体化の中の呼び出しも拾います。問い合わせには `../query` の `ltq` を使います(TSV を `rg` で直接引くこともできます)。

設計の背景は `../docs/design.md` を参照してください。

## 前提

- Windows と Visual Studio 2026(18)の C++ ツール。CMake と Ninja は VS 同梱のものを使います
- LLVM の公式配布物 `clang+llvm-<版>-x86_64-pc-windows-msvc.tar.xz` を展開したフォルダ(`include`・`lib`・`bin` が要ります)。動作を確かめた版は 23.1.4 です
  - MSI 版は開発用のライブラリを含まないので使えません

## ビルド

`build.bat` をダブルクリックします。`build\ltidx.exe` ができます(Release、並列 4)。
LLVM の場所は環境変数 `LLVM_ROOT` で指定します(既定は `E:\tools\llvm-23.1.4`)。

```bat
set LLVM_ROOT=D:\llvm\clang+llvm-23.1.4-x86_64-pc-windows-msvc
build.bat
```

## テスト

`test\run_test.bat` をダブルクリックします。`test\sample.cpp` を索引にして、`test\expected*.tsv` と一致すれば `PASS` と出ます。
コメント内・無効な `#if` の中・テンプレート内の using 宣言越しの呼び出し・未解決の呼び出しを含みます。

## 使い方

```bat
ltidx.exe -p <compile_commands.json のあるフォルダ> -root <索引に含めるソースのルート> -o <出力.tsv> [-j 並列数] [-delayed] [-no-inst] [-no-dep] [対象ファイル...]
```

- 優先度を下げて実行するには `start "" /low /wait /b ltidx.exe ...` を使います
- 出力は 3 つです
  - `<出力.tsv>` … `記号 <TAB> 役割 <TAB> 呼び出し元 <TAB> path:line:col <TAB> ソース行`
    - 役割は `def` `decl` `call` `write` `ref` `dep` のどれかです。暗黙の参照は `-implicit` が付きます
    - 呼び出し元は、その参照を含む関数です
    - 記号は修飾名で、テンプレート引数は付けません(`Foo<int>::f` は `Foo::f`)
    - `dep` は、テンプレートの中で解決先が決まらない呼び出し・参照です(`t.f()`、`this->f()`、`Base::f()` など)。記号は `?::f` の形で、名前しか分かりません。`-no-dep` を付けると出しません
    - テンプレート内の `using Base::f;` を経由した参照は、clang の索引では using 宣言(`派生::f`)にしか向きません。ltidx は TU の終わりにそのクラステンプレートの実体化を調べ、宣言の先(`基底::f`)の行も `-using` を付けて出します
  - `<出力.tsv>.tu.tsv` … TU ごとの成否・エラー数・秒数・新規行数・最初のエラー。**失敗した TU の参照は索引に無い**ので、検索結果の網羅度はここで確かめます
  - `<出力.tsv>.skip.tsv` … `path <TAB> 種類 <TAB> 開始 line:col <TAB> 終了 line:col`。種類は `comment`(コメント)か `inactive`(無効な `#if` の分岐)です。`ltq` が、テキスト検索の一致が索引に無い理由を答えるのに使います
- `-delayed` を付けないときは `/Zc:twoPhase` を足し、遅延テンプレート解析を止めます。これで、一度も実体化されないテンプレート本体の非依存呼び出しも索引に載ります
- `-no-inst` は比較用です。テンプレートの暗黙の実体化を索引しません
- PCH の指定(`/Yu` `/Yc` `/Fp`)は外して解析します

TSV を直接引く例:

```bat
rg "^Circle::update\tcall" index.tsv
rg "\tcall\tmain\t" index.tsv
```

## 制約

- 配布物の LLVM は zlib / zstd を同梱しないため、`zstub.c` で代替しています。`crc32` は zlib と同じ結果を返します。圧縮・展開は常に失敗を返しますが、ltidx は使いません
- 同じ行が複数の TU から出たときは 1 行にまとめます。構成(Debug/Release など)ごとの索引の統合はまだありません
- `-using` の解決は、その TU にあるクラステンプレートの実体化から引きます。実体化が無いときや、using 宣言が部分特殊化の中にあるときは、using 宣言の行だけが残ります
