# プロジェクトの索引を作る: <Root>\.index\index.tsv(と .tu.tsv / .skip.tsv)
# ltidx を最低優先度で実行する。引数を省くと尋ねる(ダブルクリック用)。
param(
    [string]$Root,                  # ソースのルート(索引のパスの起点。MCP サーバーもここで動かす)
    [string]$Cdb,                   # compile_commands.json のあるフォルダ(既定 <Root>)
    [int]$Jobs = [Math]::Max(1, [int]([Environment]::ProcessorCount / 2))
)
$ErrorActionPreference = 'Stop'
if (-not $Root) {
    $Root = Read-Host 'ソースのルート'
    if (-not $Cdb) { $Cdb = Read-Host 'compile_commands.json のあるフォルダ(空ならルート)' }
}
if (-not $Cdb) { $Cdb = $Root }
$Root = (Resolve-Path $Root).Path
$Cdb = (Resolve-Path $Cdb).Path
if (-not (Test-Path "$Cdb\compile_commands.json")) { throw "compile_commands.json not found in $Cdb" }
$exe = Join-Path $PSScriptRoot '..\indexer\build\ltidx.exe'
if (-not (Test-Path $exe)) { throw "ltidx.exe not found. Run indexer\build.bat first." }

New-Item -ItemType Directory "$Root\.index" -Force | Out-Null
$out = "$Root\.index\index.tsv"
$t0 = Get-Date
$p = Start-Process -FilePath (Resolve-Path $exe) -ArgumentList '-p', "`"$Cdb`"", '-root', "`"$Root`"", '-o', "`"$out`"", '-j', $Jobs `
    -NoNewWindow -PassThru -RedirectStandardError "$Root\.index\ltidx.log"
$p.PriorityClass = 'Idle'
$p.WaitForExit()
Get-Content "$Root\.index\ltidx.log" -Tail 1
"{0} 秒。索引: {1}" -f [int]((Get-Date) - $t0).TotalSeconds, $out
if ($p.ExitCode -ne 0) { "解析にエラーのあった TU があります(.index\index.tsv.tu.tsv の ok=0)。その TU だけにある参照は索引に入りません" }
