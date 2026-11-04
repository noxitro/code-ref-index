# scripts — 索引を作る・Copilot に登録する

## 目的

対象プロジェクトの索引を、手作業を少なく作るための .bat です。

| ファイル | 内容 |
|---|---|
| `index-project.bat` | 索引を `<ルート>\.index\` に作る。`compile_commands.json` が無ければ `make-cdb` で作ってから解析する |
| `make-cdb.bat` | Visual Studio のソリューション・プロジェクトか CMake のプロジェクトから、`compile_commands.json` を作る(ビルドはしない) |
| `register-copilot.bat` | Copilot CLI のユーザー設定に MCP サーバーを登録する |

## 前提

- Windows、Visual Studio(C++ のツール)、PowerShell 7(`pwsh`)
- `indexer\build.bat` でビルドした `ltidx.exe`(`index-project` が使う)

## 使い方

### いちばん簡単な使い方

`index-project.bat` をダブルクリックし、ソースのルートを入れます。ルートの直下から、次の順に探して `compile_commands.json` を作ります。

1. `.slnx`(1 つだけのとき)
2. `.sln`(1 つだけのとき)
3. `CMakeLists.txt`
4. `.vcxproj`(1 つだけのとき)

ルートに `compile_commands.json` があれば、それを使います。

### 指定するとき

```bat
index-project.bat -Root D:\src\game
index-project.bat -Root D:\src\game -Project D:\src\game\Game.sln -Configuration Release -Name release
index-project.bat -Root D:\src\game -Update
index-project.bat -Root D:\src\lib -CMakeArgs -DBUILD_EXAMPLES=ON
index-project.bat -Root D:\src\game -Cdb D:\build\game
```

| 引数 | 内容 |
|---|---|
| `-Root` | ソースのルート。索引のパスの起点で、MCP サーバーもここで動かす |
| `-Project` | `compile_commands.json` を作る元(`.slnx`・`.sln`・`.vcxproj`・`CMakeLists.txt`)。ルートの直下に複数あるときに指定する |
| `-Configuration` / `-Platform` | 構成とプラットフォーム(既定 `Debug` / `x64`)。`.sln` では、ソリューションの構成から各プロジェクトの構成への対応を使う |
| `-CMakeArgs` | CMake の構成に足す引数 |
| `-Cdb` | 手元の `compile_commands.json` のあるフォルダ。指定すると作らない |
| `-Name` | 索引の名前(既定 `index`)。構成ごとに名前を変えて作ると、ltq と MCP サーバーがまとめて引く |
| `-Update` | 前回の後に変わったファイルを読む TU・新しい TU・コマンドの変わった TU だけを解析し直す |
| `-Jobs` | 並列数(既定は論理コア数の半分)。解析は最低優先度で走る |

`compile_commands.json` は毎回作り直します(Visual Studio のソリューションなら数秒)。増えた・消えたソースも拾えます。作ったものは `<ルート>\.index\cdb-<構成>-<プラットフォーム>\` に置きます。

`make-cdb.bat` だけを使うこともできます(引数は `-Root`・`-Project`・`-Configuration`・`-Platform`・`-CMakeArgs`・`-Out`)。

## 仕組みと制約

- **Visual Studio**: MSBuild の `GetClCommandLines` ターゲット(Visual Studio が IntelliSense に使うもの)を、全プロジェクトに 1 回の MSBuild でまとめて呼び、各 .cpp の cl のコマンドラインを取り出します。プロジェクトのプロパティの設定(include パス・マクロ・言語の版・強制インクルードなど)は、cl に渡るとおりに入ります
  - 「VC++ ディレクトリ」の include パス(`IncludePath`・`ExternalIncludePath`)は cl のコマンドラインに無いので、`/imsvc` で足します
  - `.slnx` ではソリューションと同じ構成・プラットフォーム名でプロジェクトを読みます(`x86` は `Win32`)
- **CMake**: Visual Studio 同梱の CMake と Ninja で、構成だけを `<出力先>\cmake` に行います(`CMAKE_EXPORT_COMPILE_COMMANDS=ON`)
- どちらもビルドはしません。**ビルドで生成されるヘッダー**(コード生成・シェーダーの埋め込みなど)は無いままなので、それを読む TU は解析に失敗します。一度ビルドしてから索引を作ると解決します。失敗した TU は ltq の出力の先頭に出ます
- MSBuild の中間ファイルのパスが 260 文字を超えると、CMake の Visual Studio ジェネレーターなどが失敗します。深いフォルダでは短いパスに置いてください

## テスト

`test\run_test.bat` をダブルクリックします。一時フォルダに `.vcxproj`・`.slnx`・`.sln`・CMake の小さなプロジェクトを作り、`compile_commands.json` の中身(include パス・マクロ・構成の対応)と、`index-project` だけで索引ができることを確かめます。Visual Studio の C++ ツールが要ります。
