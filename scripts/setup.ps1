# 使い始めの手順をまとめて行う: LLVM の場所を決める → ltidx をビルドする → (ルートを入れれば)索引を作る。
# 引数を省くと、LLVM の場所とソースのルートを尋ねる(ダブルクリック用)。ルートを空のまま Enter で、索引づくりは飛ばす。
# LLVM の場所は ..\indexer\llvm-root.txt に書き残す(以後 indexer\build.bat を単独で叩いても同じ場所を使う)。
[CmdletBinding()]  # 知らない引数をエラーにする(黙って捨てない)
param(
    [string]$LlvmRoot,              # 公式リリースの clang+llvm-<版>-x86_64-pc-windows-msvc を展開したフォルダ
    [string]$Root,                  # 索引を作るソースのルート(省くと尋ねる。空なら索引づくりを飛ばす)
    [switch]$NoIndex                # 索引づくりを飛ばす(ルートを尋ねない)
)
$ErrorActionPreference = 'Stop'
$indexer = (Resolve-Path (Join-Path $PSScriptRoot '..\indexer')).Path
$rootFile = Join-Path $indexer 'llvm-root.txt'
$interactive = -not $PSBoundParameters.Count
# build.bat は llvm-root.txt を set /p で読むので、cmd のコードページ(OEM)で読み書きする
$enc = [Text.Encoding]::GetEncoding([Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage)

# 1. LLVM の場所
$current = $env:LLVM_ROOT
$from = '環境変数 LLVM_ROOT'
if (-not $current -and (Test-Path -LiteralPath $rootFile)) {
    $current = @([IO.File]::ReadAllLines($rootFile, $enc))[0]  # ReadLines は途中でやめるとファイルを開いたままにする
    $from = 'indexer\llvm-root.txt'
}
if (-not $LlvmRoot) {
    if ($current) { "今の LLVM の場所: $current($from)" }
    $LlvmRoot = Read-Host 'LLVM を展開したフォルダ(空のまま Enter で今の場所)'
    if (-not $LlvmRoot) { $LlvmRoot = $current }
}
if (-not $LlvmRoot) { throw 'LLVM の場所を入れてください(clang+llvm-<版>-x86_64-pc-windows-msvc を展開したフォルダ)' }
$LlvmRoot = $LlvmRoot.Trim().Trim('"').TrimEnd('\')
if (-not (Test-Path -LiteralPath (Join-Path $LlvmRoot 'lib\cmake\clang\ClangConfig.cmake'))) {
    throw "LLVM が見つかりません(lib\cmake\clang\ClangConfig.cmake がありません): $LlvmRoot`nMSI 版ではなく、公式リリースの .tar.xz を展開したフォルダを指定してください"
}
$LlvmRoot = (Resolve-Path -LiteralPath $LlvmRoot).Path
[IO.File]::WriteAllText($rootFile, "$LlvmRoot`r`n", $enc)
"LLVM の場所を書きました: $rootFile"
if ($env:LLVM_ROOT -and $env:LLVM_ROOT -ne $LlvmRoot) {
    "注意: 環境変数 LLVM_ROOT($env:LLVM_ROOT)が設定されています。indexer\build.bat を単独で叩くと、そちらが優先されます"
}

# 2. ltidx をビルドする(2 回目以降は変わった分だけ)
''
'== ltidx をビルドします'
$env:LLVM_ROOT = $LlvmRoot  # この実行の中だけ。llvm-root.txt と同じ場所を使わせる
& cmd /c "`"$indexer\build.bat`""
if ($LASTEXITCODE -ne 0) { throw "ltidx のビルドに失敗しました(終了コード $LASTEXITCODE)" }
"ビルドしました: $indexer\build\ltidx.exe"

# 3. 索引を作る
if ($NoIndex) { return }
if (-not $Root -and $interactive) {
    ''
    $Root = Read-Host '索引を作るソースのルート(.sln・.slnx・CMakeLists.txt のあるフォルダ。空のまま Enter で飛ばす)'
}
if (-not $Root) {
    '索引づくりは飛ばしました。後で scripts\index-project.bat で作れます'
    return
}
''
'== 索引を作ります'
& (Join-Path $PSScriptRoot 'index-project.ps1') -Root $Root.Trim().Trim('"')
''
'次の手順: Copilot CLI への登録は scripts\register-copilot.bat、使い方の指示は scripts\setup-instructions.bat'
