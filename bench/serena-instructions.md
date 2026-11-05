# C++ のシンボルの検索(Serena)

このフォルダの C++ は、clangd(language server)で解析した結果を、MCP サーバー `serena` のツールで引けます。C++ の呼び出し箇所・参照・定義を探すときは、ソースを grep する前に、まず Serena を使ってください。

- 最初に `initial_instructions` を呼び、Serena の使い方を読んでください
- 参照の検索(`find_referencing_symbols` など)は、clangd が型を解決した結果です。同じ名前の別のクラスのメンバーと区別できます
