# bench — Copilot CLI での検索手段の比較

## 目的

同じ問題を GitHub Copilot CLI に解かせ、検索手段ごとに正答率・往復回数・トークン・credits を比べます。条件は次のとおりです。

- A: rg のみ
- B: clangd の LSP
- C: ltidx の TSV 索引を rg で引く(使い方の指示付き)
- D: LSP と、LSP を使えという指示
- E: `../query` の CLI `ltq`(使い方の指示付き)。シェルを渡しますが、承認するのは `ltq` だけです

結果と考察は `../docs/design.md` にあります。問題は、公開されている C++ のゲームエンジン [Nox](https://github.com/noxitro/Nox) の 4 問です。

## 前提

- Copilot CLI(`copilot`)にサインイン済みであること。既定のモデルは gpt-6-luna です
- 解析対象の Nox のコピー(作業フォルダ)と、その `.cdb\compile_commands.json`。`run.ps1` は作業フォルダの `.github\` と `.index\` を書き換えるので、元のリポジトリではなくコピーを使います
- 条件 C・E は、ltidx で作った索引(`<名前>.tsv` と `.tu.tsv`・`.skip.tsv`)
- 条件 B・D は clangd(`-Clangd` でパスを指定。既定は PATH 上の `clangd`)

## 使い方

```powershell
$env:CRI_BENCH_WORKSPACE = 'D:\work\nox-copy'   # 作業フォルダ
$env:CRI_BENCH_INDEXDIR  = 'D:\work\index'      # 索引の置き場所
pwsh -File run.ps1 -Cond A -TaskId T3 -Rep 1
pwsh -File run.ps1 -Cond E -TaskId T3 -Rep 1 -Round v3 -Index nox-v3.tsv
python -I collect.py .
```

- `run.ps1` は、条件ごとの `.github\`(と `.index\`)を作業フォルダに置き直してから、1 問を実行します
  - Copilot に渡すのは読み取り専用のツール(`view` `rg` `grep` `glob` `lsp`)だけで、書き込みは渡しません
  - シェルは条件 E だけで渡し、`--allow-tool 'shell(ltq)'` で `ltq` 以外のコマンドは承認しません(非対話モードなので拒否されます)
  - セッションは `runs\index.jsonl` に記録し、やり取りの Markdown は `runs\` に出します(`runs\` はリポジトリに入れません)
- `-Round` は計測の回です。v1 は最初の計測、v2 は `dep` 行と指示の改善後、v3 は ltq です。集計は回ごとに出ます
- `-Index` は、条件 C・E で使う索引のファイル名です(`-IndexDir` 配下)
- `collect.py` は `~/.copilot/session-store.db` と、各セッションの `events.jsonl` を読み取り専用で開き、`runs\summary.tsv` と条件ごとの平均を出します。`rep=0` は試運転として集計から外します
- 問題と正解は `tasks.json`、回答の書式は `answer-format.md` です。指示は、索引用が `index-instructions.md`(v1 の計測で使ったものは `index-instructions-v1.md`)、LSP 用が `lsp-instructions.md`、ltq 用が `cli-instructions.md` です

## 注意

- 実行のたびに Copilot の credits を消費します(gpt-6-luna で 1 問 0.1〜0.7 credits)
- 正解の行番号は、計測時の Nox の作業ツリー(2026-11 時点、未コミットの変更を含む)に対するものです。今の Nox では行がずれていることがあります
