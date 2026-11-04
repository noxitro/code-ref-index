# compile_commands.json を作る: <Out>\compile_commands.json(既定 <Root>\.index\cdb-<構成>-<プラットフォーム>)
# 対象は Visual Studio のソリューション(.slnx / .sln)・プロジェクト(.vcxproj)か、CMake のプロジェクト(CMakeLists.txt)。
# -Project を省くと、ルートの直下から .slnx → .sln → CMakeLists.txt → .vcxproj の順に探す。
# Visual Studio: MSBuild の GetClCommandLines(VS の IntelliSense が使うターゲット)で、各 .cpp の cl のコマンドラインを取り出す。
#   ビルドはしない。「VC++ ディレクトリ」の include パス(IncludePath / ExternalIncludePath)はコマンドラインに無いので /imsvc で足す。
# CMake: VS 同梱の CMake と Ninja で構成だけ行う(CMAKE_EXPORT_COMPILE_COMMANDS=ON)。ビルドで生成されるヘッダーは無いままになる。
param(
    [string]$Root,                      # ソースのルート
    [string]$Project,                   # .slnx / .sln / .vcxproj / CMakeLists.txt(のフォルダ)。省くと探す
    [string]$Configuration = 'Debug',
    [string]$Platform = 'x64',
    [string]$Out,                       # compile_commands.json を置くフォルダ
    [string[]]$CMakeArgs = @()          # CMake の構成に足す引数(例: -DBUILD_EXAMPLES=ON)
)
$ErrorActionPreference = 'Stop'
if (-not $Root) { $Root = Read-Host 'ソースのルート' }
$Root = (Resolve-Path $Root).Path
if (-not $Out) { $Out = Join-Path $Root ".index\cdb-$Configuration-$Platform" }

function Find-Project {
    foreach ($pat in '*.slnx', '*.sln', 'CMakeLists.txt', '*.vcxproj') {
        $f = @(Get-ChildItem -LiteralPath $Root -Filter $pat -File)
        if ($f.Count -eq 1) { return $f[0].FullName }
        if ($f.Count -gt 1) { throw "$pat が複数あります。-Project で選んでください: $($f.Name -join ', ')" }
    }
    throw "ルートの直下に .slnx / .sln / CMakeLists.txt / .vcxproj がありません。-Project で指定してください"
}
if (-not $Project) { $Project = Find-Project }
$Project = (Resolve-Path $Project).Path
if (Test-Path -LiteralPath $Project -PathType Container) { $Project = Join-Path $Project 'CMakeLists.txt' }

$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vs) { throw 'C++ のツールを入れた Visual Studio が見つかりません' }
New-Item -ItemType Directory $Out -Force | Out-Null
$cdb = Join-Path $Out 'compile_commands.json'

if ((Split-Path $Project -Leaf) -eq 'CMakeLists.txt') {
    $cm = "$vs\Common7\IDE\CommonExtensions\Microsoft\CMake"
    $src = Split-Path $Project
    $build = Join-Path $Out 'cmake'
    # cmake と ninja はフルパスで渡す(cmd の 1 行の %PATH% は vcvarsall の前に展開されるので、PATH は書き換えない)
    $cfg = "`"$cm\CMake\bin\cmake.exe`" -S `"$src`" -B `"$build`" -G Ninja `"-DCMAKE_MAKE_PROGRAM=$cm\Ninja\ninja.exe`"" +
           " -DCMAKE_BUILD_TYPE=$Configuration -DCMAKE_EXPORT_COMPILE_COMMANDS=ON $($CMakeArgs -join ' ')"
    # vcvarsall.bat は中で vswhere.exe を PATH から呼ぶ
    $env:PATH = "$(Split-Path $vswhere);$env:PATH"
    cmd /c "call `"$vs\VC\Auxiliary\Build\vcvarsall.bat`" $Platform >nul && $cfg"
    if ($LASTEXITCODE -ne 0) { throw "CMake の構成に失敗しました(終了コード $LASTEXITCODE)" }
    Copy-Item (Join-Path $build 'compile_commands.json') $cdb -Force
} else {
    # 対象の .vcxproj と、そのプロジェクトの構成・プラットフォーム
    $projs = @()
    $ext = [IO.Path]::GetExtension($Project).ToLower()
    $solPlat = if ($Platform -eq 'x86') { 'Win32' } else { $Platform }
    if ($ext -eq '.vcxproj') {
        $projs += [pscustomobject]@{ Path = $Project; Cfg = $Configuration; Plat = $solPlat }
    } elseif ($ext -eq '.slnx') {
        $dir = Split-Path $Project
        foreach ($p in ([xml](Get-Content -LiteralPath $Project -Raw)).SelectNodes('//Project')) {
            if ($p.Path -like '*.vcxproj') {
                $projs += [pscustomobject]@{ Path = [IO.Path]::GetFullPath((Join-Path $dir $p.Path)); Cfg = $Configuration; Plat = $solPlat }
            }
        }
    } elseif ($ext -eq '.sln') {
        $dir = Split-Path $Project
        $text = Get-Content -LiteralPath $Project -Raw
        $guids = @{}
        foreach ($m in [regex]::Matches($text, 'Project\("\{[^}]+\}"\)\s*=\s*"[^"]*",\s*"([^"]+\.vcxproj)",\s*"(\{[^}]+\})"')) {
            $guids[$m.Groups[2].Value.ToUpper()] = [IO.Path]::GetFullPath((Join-Path $dir $m.Groups[1].Value))
        }
        # ソリューションの構成から各プロジェクトの構成へ(例: Debug|x86 -> Debug|Win32)。対応が無いプロジェクトは同じ名前で読む
        foreach ($g in $guids.Keys) {
            $re = [regex]::Escape($g) + '\.' + [regex]::Escape("$Configuration|$Platform") + '\.ActiveCfg\s*=\s*([^|\r\n]+)\|([^\r\n]+)'
            $m = [regex]::Match($text, $re, 'IgnoreCase')
            $projs += if ($m.Success) { [pscustomobject]@{ Path = $guids[$g]; Cfg = $m.Groups[1].Value.Trim(); Plat = $m.Groups[2].Value.Trim() } }
                      else { [pscustomobject]@{ Path = $guids[$g]; Cfg = $Configuration; Plat = $solPlat } }
        }
    } else {
        throw "対応していない種類です: $Project"
    }
    if (-not $projs) { throw "C++ のプロジェクト(.vcxproj)がありません: $Project" }

    # 全プロジェクトの GetClCommandLines を、1 回の MSBuild で並列に呼ぶ
    $proj = Join-Path $Out 'get-cl.proj'
    $items = ($projs | ForEach-Object {
        "    <P Include=`"$([Security.SecurityElement]::Escape($_.Path))`" Properties=`"Configuration=$($_.Cfg);Platform=$($_.Plat)`" />"
    }) -join "`n"
    @"
<Project>
  <ItemGroup>
$items
  </ItemGroup>
  <Target Name="Cl" Returns="@(Cl)">
    <MSBuild Projects="@(P)" Targets="GetClCommandLines;GetIncludePath" BuildInParallel="true" ContinueOnError="true">
      <Output TaskParameter="TargetOutputs" ItemName="Cl" />
    </MSBuild>
  </Target>
</Project>
"@ | Set-Content -LiteralPath $proj -Encoding utf8
    # GetIncludePath は各プロジェクトの IncludePath と ExternalIncludePath を返す(下の props で足す)
    $props = Join-Path $Out 'get-include.targets'
    @'
<Project>
  <Target Name="GetIncludePath" Returns="@(_CriInclude)">
    <ItemGroup>
      <_CriInclude Include="$(MSBuildProjectFullPath)" IncludePath="$(IncludePath);$(ExternalIncludePath)" />
    </ItemGroup>
  </Target>
</Project>
'@ | Set-Content -LiteralPath $props -Encoding utf8
    $msb = "$vs\MSBuild\Current\Bin\amd64\MSBuild.exe"
    $json = & $msb $proj -nologo -m -getTargetResult:Cl "-p:ForceImportAfterCppTargets=$props"
    if ($LASTEXITCODE -ne 0) { $json | Select-Object -Last 20; throw "MSBuild に失敗しました(終了コード $LASTEXITCODE)" }
    $res = ($json -join "`n") | ConvertFrom-Json
    $all = @($res.TargetResults.Cl.Items)
    $inc = @{}
    foreach ($i in $all | Where-Object { $_.PSObject.Properties.Name -contains 'IncludePath' }) {
        $inc["$($i.MSBuildSourceProjectFile)".ToLower()] = @($i.IncludePath -split ';' | Where-Object { $_ } | Select-Object -Unique)
    }
    $entries = [Collections.Generic.List[object]]::new()
    $failed = @()
    foreach ($i in $all | Where-Object { $_.PSObject.Properties.Name -contains 'Files' }) {
        $extra = ''
        $key = "$($i.MSBuildSourceProjectFile)".ToLower()
        if ($inc.ContainsKey($key)) { $extra = ($inc[$key] | ForEach-Object { " `"/imsvc$($_.TrimEnd('\'))`"" }) -join '' }
        foreach ($f in $i.Files -split ';' | Where-Object { $_ }) {
            $entries.Add([ordered]@{ directory = $i.WorkingDirectory; file = $f; command = "cl.exe $($i.Identity)$extra `"$f`"" })
        }
    }
    foreach ($p in $projs) {
        if (-not ($all | Where-Object { "$($_.MSBuildSourceProjectFile)" -eq $p.Path })) { $failed += $p.Path }
    }
    if ($entries.Count -eq 0) { throw 'cl のコマンドラインが 1 つも取れませんでした' }
    ConvertTo-Json -InputObject $entries.ToArray() -Depth 3 | Set-Content -LiteralPath $cdb -Encoding utf8
    if ($failed) { "コマンドラインが取れなかったプロジェクト($($failed.Count) 件): $($failed -join ', ')" }
}
$n = @((Get-Content -LiteralPath $cdb -Raw | ConvertFrom-Json)).Count
"compile_commands.json: $cdb($n TU)"
