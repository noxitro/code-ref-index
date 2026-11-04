# Copilot への使い方の指示を、プロジェクトの .github\copilot-instructions.md に足す・消す。
# 足す中身は ..\bench\mcp-instructions.md。目印のコメント(cxxref:begin / cxxref:end)で囲み、消すときはその間だけを消す。
# 引数を省くと、ソースのルートと、足すか消すかを尋ねる(ダブルクリック用)。
# 目印の外は書き換えない。改行(CRLF/LF)と BOM の有無は、元のファイルに合わせる。
[CmdletBinding()]  # 知らない引数をエラーにする(黙って捨てない)
param(
    [string]$Root,                          # ソースのルート(.github のあるフォルダ)
    [ValidateSet('', 'Add', 'Remove')]
    [string]$Action = ''                    # Add: 足す(入っていれば今の中身に差し替える)。Remove: 消す
)
$ErrorActionPreference = 'Stop'
$Begin = '<!-- cxxref:begin (setup-instructions.bat が管理。消すときもこの .bat で) -->'
$End = '<!-- cxxref:end -->'
$Block = '(?s)(\r?\n)?<!-- cxxref:begin[^\n]*?-->.*?<!-- cxxref:end -->[ \t]*(\r?\n)?'

if (-not $Root) { $Root = Read-Host 'ソースのルート' }
$Root = (Resolve-Path $Root.Trim('"')).Path
$file = Join-Path $Root '.github\copilot-instructions.md'

$text = ''
$bom = $false
if (Test-Path -LiteralPath $file) {
    $bytes = [IO.File]::ReadAllBytes($file)
    $bom = $bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF
    $text = [Text.UTF8Encoding]::new($false).GetString($bytes, [int]$bom * 3, $bytes.Length - [int]$bom * 3)
}
$has = $text -match $Block

if (-not $Action) {
    "指示のファイル: $file"
    if ($has) { '今の状態: 入っています' } else { '今の状態: 入っていません' }
    $c = Read-Host '1: 足す(入っていれば最新に差し替える)  2: 消す  (番号を入力)'
    $Action = switch ($c.Trim()) { '1' { 'Add' } '2' { 'Remove' } default { throw "1 か 2 を入力してください: $c" } }
}

function Save([string]$s) {
    New-Item -ItemType Directory (Split-Path $file) -Force | Out-Null
    [IO.File]::WriteAllText($file, $s, [Text.UTF8Encoding]::new($bom))
}

$nl = if ($text -match '\r\n') { "`r`n" } else { "`n" }
if ($Action -eq 'Add') {
    $src = Join-Path $PSScriptRoot '..\bench\mcp-instructions.md'
    $body = ([IO.File]::ReadAllText($src)).Trim() -replace '\r?\n', $nl
    $new = "$Begin$nl$body$nl$End$nl"
    if ($has) {
        # 前後の改行は残し、目印の間だけを差し替える
        $text = [regex]::Replace($text, $Block, { param($m) $m.Groups[1].Value + $new }, 'None')
        Save $text
        "差し替えました: $file"
    } else {
        if ($text.Length -gt 0 -and -not $text.EndsWith("`n")) { $text += $nl }
        if ($text.Length -gt 0) { $text += $nl }  # 前の中身との間に空行を置く
        Save ($text + $new)
        "足しました: $file"
    }
} else {
    if (-not $has) { "入っていないので、何もしません: $file"; return }
    $text = [regex]::Replace($text, $Block, '')
    if ($text.Trim().Length -eq 0) {
        # 足したときにファイルを作ったので、空になったら消して元に戻す
        Remove-Item -LiteralPath $file
        "消しました(ほかに中身が無いので、ファイルごと消しました): $file"
    } else {
        Save $text
        "消しました: $file"
    }
}
