# 条件 A(rg のみ)/ B(LSP)/ C(索引)/ D(LSP + 指示)/ E(ltq CLI)/ F(MCP)/ G(MCP + 指示)で
# Copilot CLI に 1 問を解かせ、セッションを記録する。
# Copilot に渡すのは読み取り専用のツール(view / rg / grep / glob / lsp / MCP)だけ。書き込みは渡さない。
# シェルは条件 E だけで渡し、実行を許すのは ltq コマンドだけにする。
param(
    [Parameter(Mandatory)][ValidateSet('A', 'B', 'C', 'D', 'E', 'F', 'G')][string]$Cond,
    [Parameter(Mandatory)][string]$TaskId,
    [int]$Rep = 1,
    [string]$Model = 'gpt-6-luna',
    [string]$Round = 'v1',          # 計測の回(v2: dep 行と指示の改善後、v3: ltq)
    [string]$Index = 'nox.tsv',     # 条件 C・E・F・G で使う索引($IndexDir 配下のファイル名)
    [string]$Workspace = $env:CRI_BENCH_WORKSPACE,  # 解析対象のソースのコピー(.github と .index を書き換える)
    [string]$IndexDir = $env:CRI_BENCH_INDEXDIR,    # ltidx で作った索引の置き場所
    [string]$Clangd = 'clangd',     # 条件 B・D の clangd
    [string]$Tasks = 'tasks.json',          # 問題と正解(bench 配下。別のコードベースは tasks-<名前>.json)
    [string]$Format = 'answer-format.md'    # 回答の書式(bench 配下)
)
$ErrorActionPreference = 'Stop'
$bench = $PSScriptRoot
if (-not $Workspace -or -not (Test-Path $Workspace)) { throw 'set -Workspace (or CRI_BENCH_WORKSPACE)' }
if ($Cond -in 'C', 'E', 'F', 'G' -and (-not $IndexDir -or -not (Test-Path "$IndexDir\$Index"))) {
    throw "index not found: $IndexDir\$Index (set -IndexDir or CRI_BENCH_INDEXDIR)"
}
$ws = $Workspace
$ltq = (Resolve-Path (Join-Path $bench '..\query')).Path
$tools = 'view', 'rg', 'grep', 'glob', 'lsp'
$allow = $tools

function Write-LspJson {
    $j = Get-Content "$bench\lsp.json" -Raw -Encoding utf8 | ConvertFrom-Json
    $j.lspServers.clangd.command = $Clangd
    $j | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 "$ws\.github\lsp.json"
}

# 条件ごとの設定を作業フォルダに置く(他の条件の物は消す)
foreach ($p in "$ws\.github", "$ws\.index", "$ws\.mcp.json") { if (Test-Path $p) { Remove-Item -Recurse -Force $p } }
switch ($Cond) {
    'B' {
        New-Item -ItemType Directory "$ws\.github" | Out-Null
        Write-LspJson
    }
    'C' {
        New-Item -ItemType Directory "$ws\.github", "$ws\.index" | Out-Null
        Copy-Item "$bench\index-instructions.md" "$ws\.github\copilot-instructions.md"
        Copy-Item "$IndexDir\$Index" "$ws\.index\nox.tsv"
        Copy-Item "$IndexDir\$Index.tu.tsv" "$ws\.index\nox.tsv.tu.tsv"
        # コピー直後は読めないことがあった(T4-C-r1 で rg が一致なし)。一度読み切ってから始める
        Get-FileHash "$ws\.index\nox.tsv" | Out-Null
    }
    'D' {
        # B に「LSP を使え」という指示を足した条件(C と指示の有無をそろえる)
        New-Item -ItemType Directory "$ws\.github" | Out-Null
        Write-LspJson
        Copy-Item "$bench\lsp-instructions.md" "$ws\.github\copilot-instructions.md"
    }
    'E' {
        # 段階 2 の CLI。索引(本体・TU・コメントと無効領域)を置き、ltq だけを実行可にする
        New-Item -ItemType Directory "$ws\.github", "$ws\.index" | Out-Null
        Copy-Item "$bench\cli-instructions.md" "$ws\.github\copilot-instructions.md"
        foreach ($s in '', '.tu.tsv', '.skip.tsv') { Copy-Item "$IndexDir\$Index$s" "$ws\.index\nox.tsv$s" }
        Get-FileHash "$ws\.index\nox.tsv" | Out-Null
        $tools = 'view', 'rg', 'grep', 'glob', 'powershell'
        $allow = 'view', 'rg', 'grep', 'glob', 'shell(ltq)', 'shell(ltq:*)'
    }
    { $_ -in 'F', 'G' } {
        # 段階 3 の MCP。作業フォルダの .mcp.json に登録する(F は指示なし、G は指示あり)。シェルは渡さない
        New-Item -ItemType Directory "$ws\.github", "$ws\.index" | Out-Null
        if ($Cond -eq 'G') { Copy-Item "$bench\mcp-instructions.md" "$ws\.github\copilot-instructions.md" }
        foreach ($s in '', '.tu.tsv', '.skip.tsv') { Copy-Item "$IndexDir\$Index$s" "$ws\.index\index.tsv$s" }
        Get-FileHash "$ws\.index\index.tsv" | Out-Null
        $server = (Resolve-Path (Join-Path $bench '..\mcp\server.py')).Path
        @{ mcpServers = @{ 'code-ref-index' = @{ type = 'local'; command = 'python'; args = @('-I', $server); tools = @('*') } } } |
            ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8 "$ws\.mcp.json"
        $tools = 'view', 'rg', 'grep', 'glob', 'code-ref-index'
        $allow = $tools
    }
}
$oldPath = $env:PATH
if ($Cond -eq 'E') { $env:PATH = "$ltq;$env:PATH" }

$task = (Get-Content "$bench\$Tasks" -Raw -Encoding utf8 | ConvertFrom-Json) | Where-Object id -eq $TaskId
if (-not $task) { throw "unknown task $TaskId" }
$prompt = $task.prompt + "`n`n" + (Get-Content "$bench\$Format" -Raw -Encoding utf8)

$name = if ($Round -eq 'v1') { "$TaskId-$Cond-r$Rep" } else { "$Round-$TaskId-$Cond-r$Rep" }
New-Item -ItemType Directory "$bench\runs" -Force | Out-Null
$t0 = Get-Date
Push-Location $ws
try {
    copilot -p $prompt --model $Model --trust-folder --available-tools @tools --allow-tool @allow -s `
        --share "$bench\runs\$name.md" --log-level error *> "$bench\runs\$name.out.txt"
    $code = $LASTEXITCODE
} finally { Pop-Location; $env:PATH = $oldPath }
$sec = [int]((Get-Date) - $t0).TotalSeconds
# --trust-folder は --session-id と併用できないので、実行後に最新のセッションを引く
$sid = (Get-ChildItem "$env:USERPROFILE\.copilot\session-state" -Directory | Where-Object LastWriteTime -ge $t0 |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1).Name
[pscustomobject]@{ name = $name; round = $Round; task = $TaskId; cond = $Cond; rep = $Rep; model = $Model; session = $sid; exit = $code; sec = $sec; at = $t0.ToString('s') } |
    ConvertTo-Json -Compress | Add-Content -Encoding utf8 "$bench\runs\index.jsonl"
"$name exit=$code ${sec}s session=$sid"
