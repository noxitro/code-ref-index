# ltidx の配布物(LLVM 無しで使える zip)を作る: ltidx.exe・clang の組み込みヘッダー(lib\clang\<版>\include)・ライセンス・版。
# CI(.github\workflows\ci.yml)がタグのときにリリースに載せ、scripts\get-ltidx.ps1 が取ってくる。手元でも作れる。
# ltidx.exe は、隣の lib\clang\<版> があればそれを組み込みヘッダーに使う(ビルドした LLVM の場所より優先)。
[CmdletBinding()]  # 知らない引数をエラーにする(黙って捨てない)
param(
    [string]$LlvmRoot = $env:LLVM_ROOT,                                  # ltidx をビルドした LLVM(組み込みヘッダーを写す)
    [string]$Ltidx = (Join-Path $PSScriptRoot '..\indexer\build\ltidx.exe'),
    [string]$OutDir = (Join-Path $PSScriptRoot '..\dist'),
    [string]$LlvmLicense,                                                # LLVM の LICENSE.TXT(あれば LICENSE-LLVM.txt として同梱)
    [string]$ThirdPartyNotices                                           # llvm-notices.py が作る表示(あれば同梱)
)
# 配って(リリースに載せて)よいのは、LICENSE-LLVM.txt と THIRD-PARTY-NOTICES.txt の両方を入れた zip だけ。CI は両方を渡す。
# THIRD-PARTY-NOTICES.txt は ltidx.exe に埋め込まれた LLVM の中の別ライセンスの部品(正規表現・xxHash・Unicode)の表示で、
# BSD はバイナリで配るときも表示を求める。他者の著作権表示なのでリポジトリには置かず、LLVM のソースから作る
$ErrorActionPreference = 'Stop'
if (-not $LlvmRoot) {
    $rootFile = Join-Path $PSScriptRoot '..\indexer\llvm-root.txt'
    $enc = [Text.Encoding]::GetEncoding([Globalization.CultureInfo]::CurrentCulture.TextInfo.OEMCodePage)
    if (Test-Path -LiteralPath $rootFile) { $LlvmRoot = @([IO.File]::ReadAllLines($rootFile, $enc))[0] }
}
if (-not $LlvmRoot) { throw 'LLVM の場所を -LlvmRoot か環境変数 LLVM_ROOT で指定してください(ltidx をビルドした LLVM)' }
$clang = Get-ChildItem -LiteralPath (Join-Path $LlvmRoot 'lib\clang') -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name | Select-Object -Last 1
if (-not $clang -or -not (Test-Path -LiteralPath (Join-Path $clang.FullName 'include'))) {
    throw "clang の組み込みヘッダーがありません: $LlvmRoot\lib\clang\<版>\include"
}
if (-not (Test-Path -LiteralPath $Ltidx)) { throw "ltidx.exe がありません: $Ltidx(indexer\build.bat でビルドしてください)" }
$version = (Get-Content -Raw -LiteralPath (Join-Path $PSScriptRoot '..\plugin.json') | ConvertFrom-Json).version

$name = 'cxxref-ltidx-win-x64'
New-Item -ItemType Directory $OutDir -Force | Out-Null
$stage = Join-Path $OutDir $name
if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
New-Item -ItemType Directory (Join-Path $stage "lib\clang\$($clang.Name)") -Force | Out-Null
Copy-Item -LiteralPath $Ltidx -Destination $stage
Copy-Item -LiteralPath (Join-Path $clang.FullName 'include') -Destination (Join-Path $stage "lib\clang\$($clang.Name)") -Recurse
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '..\LICENSE') -Destination (Join-Path $stage 'LICENSE.txt')
if ($LlvmLicense) { Copy-Item -LiteralPath $LlvmLicense -Destination (Join-Path $stage 'LICENSE-LLVM.txt') }
if ($ThirdPartyNotices) { Copy-Item -LiteralPath $ThirdPartyNotices -Destination (Join-Path $stage 'THIRD-PARTY-NOTICES.txt') }
if (-not ($LlvmLicense -and $ThirdPartyNotices)) {
    Write-Warning '-LlvmLicense か -ThirdPartyNotices が無いので、ライセンスの表示が足りません。この zip は配らないでください(手元で試すためのもの)'
}
[IO.File]::WriteAllText((Join-Path $stage 'version.txt'), "$version`n")
[IO.File]::WriteAllText((Join-Path $stage 'README.txt'),
    "cxxref $version の ltidx(clang $($clang.Name) の libtooling で作った C++ 参照索引の作成器)。`n" +
    "lib\clang\$($clang.Name)\include は clang の組み込みヘッダー(Apache License 2.0 with LLVM Exceptions)。`n" +
    "ライセンス: LICENSE.txt(cxxref、MIT)、LICENSE-LLVM.txt(LLVM)、THIRD-PARTY-NOTICES.txt(LLVM の中の別ライセンスの部品)。`n" +
    "使い方: https://github.com/noxitro/cxxref`n", [Text.UTF8Encoding]::new($false))

$zip = Join-Path $OutDir "$name.zip"
if (Test-Path -LiteralPath $zip) { Remove-Item -LiteralPath $zip -Force }
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $zip
$hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
[IO.File]::WriteAllText("$zip.sha256", "$hash  $name.zip`n")
"配布物を作りました: $zip($version、clang $($clang.Name)、sha256 $hash)"
