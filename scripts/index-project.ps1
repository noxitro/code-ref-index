# プロジェクトの索引を作る: <Root>\.index\<Name>.tsv(と .tu.tsv / .skip.tsv / .deps.tsv)
# ltidx を最低優先度で実行する。引数を省くと尋ねる(ダブルクリック用)。
# 構成(Debug/Release、プラットフォームなど)ごとに -Name を変えて作ると、ltq と MCP サーバーはすべてをまとめて引く。
# -Update を付けると、前回の後に変わったファイルを読む TU・新しい TU・コマンドの変わった TU だけを解析し直す。
param(
    [string]$Root,                  # ソースのルート(索引のパスの起点。MCP サーバーもここで動かす)
    [string]$Cdb,                   # compile_commands.json のあるフォルダ(既定 <Root>)
    [string]$Name = 'index',        # 索引の名前(構成名)。英数字・_・- のみ
    [int]$Jobs = [Math]::Max(1, [int]([Environment]::ProcessorCount / 2)),
    [switch]$Update                 # 前回の索引の後に変わった TU だけを解析し直す(初回は全体を作る)
)
$ErrorActionPreference = 'Stop'
if (-not $Root) {
    $Root = Read-Host 'ソースのルート'
    if (-not $Cdb) { $Cdb = Read-Host 'compile_commands.json のあるフォルダ(空ならルート)' }
}
if (-not $Cdb) { $Cdb = $Root }
if ($Name -notmatch '^[A-Za-z0-9_-]+$') { throw "-Name は英数字・_・- のみ: $Name" }
$Root = (Resolve-Path $Root).Path
$Cdb = (Resolve-Path $Cdb).Path
if (-not (Test-Path "$Cdb\compile_commands.json")) { throw "compile_commands.json not found in $Cdb" }
$exe = Join-Path $PSScriptRoot '..\indexer\build\ltidx.exe'
if (-not (Test-Path $exe)) { throw "ltidx.exe not found. Run indexer\build.bat first." }

New-Item -ItemType Directory "$Root\.index" -Force | Out-Null
$out = "$Root\.index\$Name.tsv"
$t0 = Get-Date
$ltidxArgs = @('-p', "`"$Cdb`"", '-root', "`"$Root`"", '-o', "`"$out`"", '-j', $Jobs)
if ($Update) {
    if (Test-Path "$out.deps.tsv") { $ltidxArgs += '-update' }
    else { '前回の索引(.deps.tsv 付き)が無いので、全体を作ります' }
}
$p = Start-Process -FilePath (Resolve-Path $exe) -ArgumentList $ltidxArgs `
    -NoNewWindow -PassThru -RedirectStandardError "$Root\.index\$Name.log"
$p.PriorityClass = 'Idle'
$p.WaitForExit()
Get-Content "$Root\.index\$Name.log" | Where-Object { $_ -notmatch '^\[' }  # TU ごとの進み具合を除く
"{0} 秒。索引: {1}" -f [int]((Get-Date) - $t0).TotalSeconds, $out
if ($p.ExitCode -eq 2) { "索引を作れませんでした(.index\$Name.log を見てください)。前の索引はそのまま残っています" }
elseif ($p.ExitCode -ne 0) { "解析にエラーのあった TU があります(.index\$Name.tsv.tu.tsv の ok=0)。その TU だけにある参照は索引に入りません" }
