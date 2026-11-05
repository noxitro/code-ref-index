"""ltidx.exe に埋め込まれる、LLVM の中の別ライセンスの部品の表示(THIRD-PARTY-NOTICES.txt)を作る。
LLVM のその版のタグのソースから原文を取ってきて写す。表示はリポジトリに置かない(CI が配布物を作るときに作る)。
BSD の部品はバイナリで配るときも表示を求め、LLVM 本体のライセンス文(LICENSE-LLVM.txt)には含まれていない。

  python -I llvm-notices.py --version 23.1.4 --out dist/THIRD-PARTY-NOTICES.txt [--from <取ってきたファイルのフォルダ>]

部品の場所や表示の形が LLVM の版で変わったら、見つからない旨を出して失敗する(黙って欠けた表示を作らない)。
"""
import argparse
import os
import re
import sys
import urllib.request

# (ファイル, 見出し, 表示の中にある目印)。目印を含むコメント(/* ... */)を表示として写す。目印が None ならファイル全体
PARTS = [
    ('COPYRIGHT.regex', 'llvm/lib/Support/regcomp.c ほか(正規表現。OpenBSD の libc/regex から)', None),
    ('xxhash.cpp', 'llvm/lib/Support/xxhash.cpp(xxHash)', 'Yann Collet'),
    ('ConvertUTF.cpp', 'llvm/lib/Support/ConvertUTF.cpp(Unicode, Inc.)', 'Distributed under the Terms of Use'),
    ('UnicodeNameToCodepointGenerated.cpp', 'llvm/lib/Support/UnicodeNameToCodepointGenerated.cpp(Unicode, Inc. のデータ)',
     'UNICODE, INC. LICENSE AGREEMENT'),
    ('MD5.cpp', 'llvm/lib/Support/MD5.cpp(パブリックドメイン)', 'Alexander Peslyak'),
]
URL = 'https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-{version}/llvm/lib/Support/{name}'


def comment_with(text, mark):
    """mark を含む最初のコメント(/* ... */)を、行頭の * を外して返す。無ければ None。"""
    i = text.find(mark)
    if i < 0:
        return None
    a, b = text.rfind('/*', 0, i), text.find('*/', i)
    if a < 0 or b < 0:
        return None
    return '\n'.join(re.sub(r'^ ?\* ?', '', line).rstrip() for line in text[a + 2:b].splitlines()).strip('\n')


def fetch(version, name, local):
    if local:
        with open(os.path.join(local, name), encoding='utf-8') as f:
            return f.read()
    with urllib.request.urlopen(URL.format(version=version, name=name), timeout=60) as r:
        return r.read().decode('utf-8')


def build(version, local=None):
    out = [f'ltidx.exe に埋め込まれた、LLVM の中の別ライセンスの部品の表示(LLVM {version}。llvmorg-{version} のソースから写した)。\n'
           'LLVM 本体のライセンス(Apache License 2.0 with LLVM Exceptions と、旧 LLVM ライセンス)は LICENSE-LLVM.txt にある。\n'
           'lib\\clang\\<版>\\include の組み込みヘッダーは手を加えずに配っており、各ファイルの冒頭に表示がある。\n\n'
           f'Notices for third-party code in LLVM that is embedded in ltidx.exe (copied from the llvmorg-{version} sources).\n'
           'The LLVM license itself is in LICENSE-LLVM.txt.\n']
    for name, title, mark in PARTS:
        text = fetch(version, name, local)
        body = text.strip('\n') if mark is None else comment_with(text, mark)
        if not body:
            raise SystemExit(f'{name}: the notice was not found (marker {mark!r}); check the file in LLVM {version}')
        out.append(f'\n{"=" * 78}\n{title}\n{"=" * 78}\n\n{body}\n')
    return ''.join(out)


def main():
    ap = argparse.ArgumentParser(prog='llvm-notices')
    ap.add_argument('--version', required=True, help='LLVM version of the release that ltidx was built with, e.g. 23.1.4')
    ap.add_argument('--out', required=True)
    ap.add_argument('--from', dest='local', help='read the files from this folder instead of GitHub')
    a = ap.parse_args()
    text = build(a.version, a.local)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, 'w', encoding='utf-8', newline='\n') as f:
        f.write(text)
    print(f'{a.out}: {len(PARTS)} notices from LLVM {a.version}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
