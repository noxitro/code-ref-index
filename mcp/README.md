# code-ref-index MCP サーバー

## 目的

ltq の問い合わせを、MCP(Model Context Protocol)のツールとして GitHub Copilot に渡します。
CLI 方式(`ltq`)と違って Copilot にシェルを渡す必要が無く、ツールはサーバー名(`code-ref-index`)で承認できます。

| ツール | 内容 |
|---|---|
| `refs` | 記号の呼び出し・参照・定義と、テンプレート内の未解決の同名呼び出し、索引に無いテキスト一致とその理由を 1 回で返す(`ltq refs` と同じ) |
| `check` | `path:line` ごとに、索引の行と、その行を含むコメント・無効な `#if` を返す(`ltq check` と同じ) |

どちらも読み取り専用です。

## 前提

- Python 3.10 以降(標準ライブラリだけを使います。追加のインストールは要りません)
- 対象プロジェクトの索引。`<ルート>\.index\index.tsv` を `..\scripts\index-project.bat` で作ります

## 起動

Copilot が起動するので、手で起動する必要はありません。手で動かすときは次のとおりです(標準入出力で JSON-RPC を話します)。

```bat
code-ref-index-mcp.cmd [--root <ソースのルート>] [--index <索引.tsv>]
```

- `--root` の既定は、起動されたときのカレントフォルダです。Copilot CLI は作業フォルダで起動します
- `--index` の既定は `<ルート>\.index\index.tsv` です。無いときは、`.index` の中の索引本体が 1 つならそれを使います
- 環境変数 `CRI_ROOT` と `CRI_INDEX` でも指定できます

## Copilot CLI で使う

すべてのプロジェクトで使う(ユーザー設定 `~/.copilot/mcp-config.json` に登録。`../scripts/register-copilot.bat` が同じことをします):

```bat
copilot mcp add code-ref-index -- python -I E:\dev\github.com\noxitro\code-ref-index\mcp\server.py
```

`.index` の無いプロジェクトでは、ツールは「索引が無い」と返すだけです。

プロジェクトごとに使う場合は、そのプロジェクトの `.mcp.json`(または `.github/mcp.json`)に書きます。ただし、Copilot CLI がこのファイルを読むのは**信頼済みのフォルダだけ**です。非対話モード(`copilot -p`)では `--trust-folder` を付けるか、事前に信頼しておきます【公式: docs.github.com の Copilot CLI「Add MCP servers」】。

```json
{
  "mcpServers": {
    "code-ref-index": {
      "type": "local",
      "command": "python",
      "args": ["-I", "E:\\dev\\github.com\\noxitro\\code-ref-index\\mcp\\server.py"],
      "tools": ["*"]
    }
  }
}
```

- ツールの名前は `code-ref-index-refs` と `code-ref-index-check` になります
- 非対話モードで承認するには `--allow-tool code-ref-index` を付けます。`--available-tools` で絞るときも `code-ref-index` を入れます
- サーバーのパスは各自の環境のものなので、リポジトリにコミットする `.mcp.json` には向きません。ユーザー設定に登録するほうが手軽です

### 使い方の指示

Copilot CLI は、許可リストに無い MCP サーバーの初期化時の説明(instructions)をシステムプロンプトに入れません。それでも計測では、指示なしで 12 回とも `refs` が最初に使われました(正答 11/12)。指示を足すと 12/12 になり、トークンも減りました(`../docs/design.md`)。指示を入れるなら、`../bench/mcp-instructions.md` の内容をプロジェクトの `.github/copilot-instructions.md` に足します。

## VS Code の Copilot で使う

`.vscode/mcp.json` に書きます(Copilot CLI はこのファイルを読みません)。VS Code での動作は未確認です。

```json
{
  "servers": {
    "code-ref-index": {
      "type": "stdio",
      "command": "python",
      "args": ["-I", "E:\\dev\\github.com\\noxitro\\code-ref-index\\mcp\\server.py", "--root", "${workspaceFolder}"]
    }
  }
}
```

## 制約

- 1 回の呼び出しごとに索引を読み直します(Nox の 41 MB で 0.2〜0.3 秒)。常駐中に索引を作り直しても、次の呼び出しから新しい索引を使います
- MCP の stdio トランスポートの、ツールに要る範囲(`initialize`・`tools/list`・`tools/call`・`ping`)だけを実装しています
