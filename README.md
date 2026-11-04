# cxxref

[![CI](https://github.com/noxitro/cxxref/actions/workflows/ci.yml/badge.svg)](https://github.com/noxitro/cxxref/actions/workflows/ci.yml)

clang(libtooling)で事前に作った C++ の参照索引を、GitHub Copilot などのエージェントが少ない往復で確実に引けるようにする道具です。

Copilot で C++ の呼び出し箇所を探すと、LSP(clangd)を渡してもほとんど使われず、使わせても ripgrep より多くのトークンを使いました。ripgrep は速いものの、同名の関数・コメント内の一致・テンプレート内の呼び出しで誤答します。このリポジトリは、clang の解析結果を事前に索引にしておき、エージェントには「1 回の呼び出しで、答えと、答えに入らない一致の理由まで」返します。

計測(Copilot CLI 1.0.105、gpt-6-luna、各問 5 回。C++ の記号の呼び出し箇所を探す問題)の結果:

| コードベース | 手段 | 正答 | 入力トークン平均 | credits 平均 |
|---|---|---|---|---|
| Nox(ゲームエンジン、367 TU。4 問) | rg だけ | 9/20 | 40k | 0.26 |
| | **MCP(使い方の指示あり)** | **20/20** | **19k(−52%)** | **0.12** |
| B(公開されている OSS の C++ ライブラリ、194 TU。4 問) | rg だけ | 20/20 | 92k | 0.52 |
| | **MCP(使い方の指示あり)** | **20/20** | **33k(−64%)** | **0.23** |
| C(公開されている大規模な OSS のコンパイラ基盤、2,350 TU。3 問) | rg だけ | 9/15 | 144k | 0.77 |
| | **MCP(使い方の指示あり)** | **15/15** | **23k(−84%)** | **0.20** |

- コードが大きいほど差が開きます。rg だけでは、同じ名前の関数が多いと取りこぼし(C の `print` は 389 種)、通読のトークンが増えます
- MCP は、使い方の指示が無くても使われます(Nox で 20 回中 19 回正答、トークン −36%)。clangd の LSP は、渡しても使われませんでした(以前の計測で 8 回中 0 回)
- rg のほうが向く問題(マクロ・文字列・`.vcxproj` の設定)では、MCP を渡しても正答は落ちず(15/15)、ツールも呼ばれませんでした。入力トークンは 4 割増えます(指示とツールの定義の分)
- 索引の後にコードを変えても、MCP サーバーが変わった TU だけを作り直してから答えます(.cpp の変更で Nox 5 秒・C 13 秒)

詳細は [docs/design.md](docs/design.md) にあります。まだ試作の段階です。

対象は C++ です(同じ clang で解析できる C と Objective-C++ のソースも索引に入ります)。ほかの言語は扱いません。

## 構成

| フォルダ | 内容 |
|---|---|
| [indexer](indexer/README.md) | `ltidx`: `compile_commands.json` の全 TU を解析し、参照の TSV を作る(C++、Windows) |
| [query](query/README.md) | `ltq`: 索引の問い合わせ CLI(Python) |
| [mcp](mcp/README.md) | MCP サーバー: `ltq` の `refs` / `check` を Copilot のツールとして渡す(Python) |
| `setup.bat` | 使い始めの手順 2〜5(LLVM の場所を決めて ltidx をビルドし、索引を作り、使い方の指示を足し、Copilot CLI に登録する)をまとめて行う |
| [scripts](scripts/README.md) | `index-project.bat`: プロジェクトの索引を作る(`compile_commands.json` が無ければ作る。`-Update` で変わった TU だけ作り直す)。`make-cdb.bat`: Visual Studio のソリューションか CMake から `compile_commands.json` を作る。`register-copilot.bat`: Copilot CLI に MCP サーバーを登録する。`setup-instructions.bat`: 使い方の指示を足す・消す |
| `run-tests.bat` | すべてのテストを流す(下の「動作確認」) |
| [bench](bench/README.md) | Copilot CLI での検索手段の比較(rg・LSP・索引・CLI・MCP) |
| [docs](docs/design.md) | 設計と計測の記録 |

## 使い始める

1. **LLVM を用意する**: 公式リリースの `clang+llvm-<版>-x86_64-pc-windows-msvc.tar.xz` を展開します(MSI 版は不可)。動作を確かめた版は 23.1.4 です

**手順 2〜5 は `setup.bat` でまとめてできます**: ダブルクリックし、LLVM の展開先とソースのルートを入れます。LLVM の場所を `indexer\llvm-root.txt` に書き、ltidx をビルドし、索引を作ります。続けて、使い方の指示を足すか、Copilot CLI に登録するかを Y/n で尋ねます(登録は、未登録か、別の場所の `server.py` で登録されているときだけ尋ねます)。2 回目からは、空のまま Enter で前の LLVM の場所を使います。ルートを空のまま Enter にすると、索引づくりと指示を飛ばします
   - 引数でも渡せます: `setup.bat -LlvmRoot <LLVM> -Root <ルート> -Instructions -RegisterCopilot`。引数を渡したときは尋ねず、指示と登録は `-Instructions`・`-RegisterCopilot` を付けたときだけ行います。`-NoIndex` で索引づくりを飛ばします

2. **ltidx をビルドする**: `indexer\build.bat` をダブルクリックします。LLVM の場所は、環境変数 `LLVM_ROOT` か、`indexer\llvm-root.txt`(1 行目に展開先のフォルダを書く。git には入りません)で指定します
3. **索引を作る**: `scripts\index-project.bat` をダブルクリックし、ソースのルート(`.sln`・`.slnx`・`CMakeLists.txt` のあるフォルダ)を入れます。`compile_commands.json` は、ソリューションか CMake から自動で作ります(ビルドはしません)。`<ルート>\.index\` に索引ができます。`.index` の中に「全部を無視する」`.gitignore` を置くので、プロジェクトの `.gitignore` に足す必要はありません
   - コードを変えた後は、MCP サーバーが呼ばれたときに、変わった TU だけを自動で作り直します([mcp/README.md](mcp/README.md) の「索引の自動更新」)。手で作り直すときは `-Update` を付けます
   - Debug/Release など構成を足すときは `-Configuration Release -Name release` のように名前を変えて作ると、まとめて引けます
   - 細かい指定(ソリューションが複数ある、CMake のオプションを渡す、手元の `compile_commands.json` を使う)は [scripts/README.md](scripts/README.md) を参照してください
4. **Copilot に登録する**: Copilot CLI なら `scripts\register-copilot.bat` をダブルクリックします(中身は次の 1 行です)。以後、どのプロジェクトでも作業フォルダの `.index` を自動で使います。作業フォルダに無ければ、親のフォルダへさかのぼって探します(ソースのルートより下で Copilot を起動しても使えます)

   ```bat
   copilot mcp add code-ref-index -- python -I <このリポジトリ>\mcp\server.py
   ```

   VS Code や、プロジェクト単位の登録は [mcp/README.md](mcp/README.md) を参照してください
   - MCP サーバーの名前は、旧名の `code-ref-index` のままです。ツールの名前(`code-ref-index-refs` など)がこの名前から付き、計測はこの名前で行ったためです
5. **使い方の指示を足す(推奨)**: `scripts\setup-instructions.bat` をダブルクリックし、ソースのルートを入れて「1: 足す」を選びます。プロジェクトの `.github\copilot-instructions.md` に [bench/mcp-instructions.md](bench/mcp-instructions.md) の内容が入ります。やめるときは同じ .bat で「2: 消す」を選ぶと、足した部分だけが消えます

## 動作確認

`run-tests.bat` をダブルクリックすると、すべてのテストが流れ、最後に `ALL PASS` と出ます。

| 対象 | 内容 | LLVM |
|---|---|---|
| ltq(`query\test`) | 合成した小さな索引で、構成の統合・行の付け直し・仮想関数・Shift_JIS のソースを確かめる | 不要 |
| MCP サーバー(`mcp\test`) | `server.py` を起動し、`initialize`・`tools/list`・`refs`・`check` を JSON-RPC で呼ぶ | 不要 |
| make-cdb・index-project(`scripts\test`) | 一時フォルダに作った `.vcxproj`・`.slnx`・`.sln`・CMake のプロジェクトから `compile_commands.json` を作り、`index-project` だけで索引ができることを確かめる | 不要(Visual Studio の C++ ツールが要る。index-project の確認には `ltidx.exe` も要る) |
| ltidx(`indexer\test`) | サンプルを索引にして期待値と比べる。Shift_JIS・UTF-16 のソース、仮想関数の上書き、`-update` と全体の作り直しの一致 | ビルド済みの `ltidx.exe` が要る(無ければ飛ばす) |

GitHub Actions でも、push と pull request のたびに同じテストを流します(ltidx は LLVM の公式配布物でビルドします)。

ltidx をビルドしたら、テストが作った索引で `ltq` を試せます。

```bat
query\ltq.cmd --root indexer\test --index indexer\test\out\sample.tsv refs A::Reset
```

`calls` に `sample.cpp:7` の 1 件、`text matches ... not in the index` にコメント内(5 行目)と無効な `#if` の中(9 行目)の一致が理由付きで出れば、動いています。

## コードが変わったとき・実行時に決まる呼び出し

- **索引の後にコードを変えた**: `ltq` と MCP サーバーは、変わったファイルの行を今のファイルから探して付け直し、`[moved from line N]` などの注記と、変わったファイルの一覧を出します。正確な結果には `scripts\index-project.bat ... -Update` で作り直します。変わったファイルを読む TU・新しい TU・コマンドの変わった TU だけを解析します(コードベース B で全体 16 秒に対し 1.5 秒)
- **仮想関数**: 基底クラスのポインタや参照越しの呼び出しは、clang が基底のメソッドに解決します。`refs` は `virtual dispatch` の節に、上書きの関係と、実行時に問い合わせの関数へ届きうる基底経由の呼び出しを出します
- **関数ポインタ・`std::function`・コールバック**: 関数を取得した場所は `other references` に出ます。どこから呼ばれるかは、索引では分かりません
- **文字列での呼び出し・リフレクション・DLL の動的ロード・スクリプトからの呼び出し**: 索引に出ません。「0 件」は「使われていない」を意味しません

## 前提

- Windows、Visual Studio 2026 の C++ ツール(ltidx のビルドと `compile_commands.json` の生成)、PowerShell 7(`pwsh`)
- Python 3.10 以降(ltq と MCP サーバー。標準ライブラリだけを使います)

## ライセンス

[MIT](LICENSE)
