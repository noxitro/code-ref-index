# code-ref-index

clang(libtooling)で事前に作った C++ の参照索引を、GitHub Copilot などのエージェントが少ない往復で確実に引けるようにする道具です。

Copilot で C++ の呼び出し箇所を探すと、LSP(clangd)を渡してもほとんど使われず、使わせても ripgrep より多くのトークンを使いました。ripgrep は速いものの、同名の関数・コメント内の一致・テンプレート内の呼び出しで誤答します。このリポジトリは、clang の解析結果を事前に索引にしておき、エージェントには「1 回の呼び出しで、答えと、答えに入らない一致の理由まで」返します。

計測(Nox の 4 問、Copilot CLI、gpt-6-luna、各 3 回)では、rg だけのときの正答 9/12・入力 45k トークンに対して、CLI 版は 12/12・20k トークンでした。詳細は [docs/design.md](docs/design.md) にあります。まだ試作の段階です。

## 構成

| フォルダ | 内容 |
|---|---|
| [indexer](indexer/README.md) | `ltidx`: `compile_commands.json` の全 TU を解析し、参照の TSV を作る(C++、Windows) |
| [query](query/README.md) | `ltq`: 索引の問い合わせ CLI(Python) |
| [mcp](mcp/README.md) | MCP サーバー: `ltq` の `refs` / `check` を Copilot のツールとして渡す(Python) |
| [scripts](scripts) | `index-project.bat`: プロジェクトの索引を `.index\index.tsv` に作る |
| [bench](bench/README.md) | Copilot CLI での検索手段の比較(rg・LSP・索引・CLI・MCP) |
| [docs](docs/design.md) | 設計と計測の記録 |

## 使い始める

1. **LLVM を用意する**: 公式リリースの `clang+llvm-<版>-x86_64-pc-windows-msvc.tar.xz` を展開します(MSI 版は不可)。動作を確かめた版は 23.1.4 です
2. **ltidx をビルドする**: `indexer\build.bat` をダブルクリックします。LLVM の場所は環境変数 `LLVM_ROOT` で指定します(既定 `E:\tools\llvm-23.1.4`)
3. **索引を作る**: 対象プロジェクトの `compile_commands.json` を用意し、`scripts\index-project.bat -Root <ソースのルート> -Cdb <compile_commands.json のフォルダ>` を実行します。`<ルート>\.index\` に索引ができます(プロジェクトの `.gitignore` に `.index/` を足してください)
4. **Copilot に登録する**: Copilot CLI なら次の 1 行です。作業フォルダの `.index` を自動で使います

   ```bat
   copilot mcp add code-ref-index -- python -I <このリポジトリ>\mcp\server.py
   ```

   VS Code や、プロジェクト単位の登録は [mcp/README.md](mcp/README.md) を参照してください
5. **使い方の指示を足す(推奨)**: [bench/mcp-instructions.md](bench/mcp-instructions.md) の内容を、プロジェクトの `.github/copilot-instructions.md` に足します

## 前提

- Windows、Visual Studio 2026 の C++ ツール(ltidx のビルド)
- Python 3.10 以降(ltq と MCP サーバー。標準ライブラリだけを使います)

## ライセンス

[MIT](LICENSE)
