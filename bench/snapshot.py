"""計測に使うソースの写し(スナップショット)を扱う。正解の行番号は写しに対するものなので、写しを固定して使い回す。

  python -I snapshot.py rebase <cdb のフォルダ> <元のルート> <新しいルート>
      compile_commands.json の中のパス(file・directory・引数)を、元のルートから新しいルートに書き換える(その場で)
  python -I snapshot.py digest <ルート>
      ソースの指紋(相対パスと中身の SHA-256 から作る)。計測の前に、記録した指紋と同じかを確かめる

指紋は、計測の道具が書き換えるもの(. で始まるフォルダ・ファイル。.index・.cdb・.github・.mcp.json など)と、
ルートの .snapshot-exclude に書いたフォルダ(ビルドの出力など)を除いて作る。
"""
import hashlib
import json
import os
import re
import sys


def rebase(cdb, old, new):
    path = os.path.join(cdb, 'compile_commands.json')
    with open(path, encoding='utf-8-sig') as f:
        entries = json.load(f)
    # 区切りは \ と / のどちらでも、大文字小文字は区別せずに置き換える
    pat = re.compile(r'[\\/]'.join(re.escape(p) for p in re.split(r'[\\/]', old.rstrip('\\/'))), re.I)
    new = new.rstrip('\\/')
    n = 0

    def sub(s):
        nonlocal n
        r, k = pat.subn(lambda m: new, s)
        n += k
        return r

    for e in entries:
        for key in ('file', 'directory', 'command', 'output'):
            if isinstance(e.get(key), str):
                e[key] = sub(e[key])
        if isinstance(e.get('arguments'), list):
            e['arguments'] = [sub(a) for a in e['arguments']]
    with open(path, 'w', encoding='utf-8', newline='\n') as f:
        json.dump(entries, f, indent=2, ensure_ascii=False)
    print(f'{path}: {len(entries)} entries, {n} paths rebased')


def digest(root):
    # ルートの .snapshot-exclude に 1 行 1 つ書いたフォルダ(ルートからの相対パス)も除く。ビルドの出力(O3DE の build/ は 27 GB)など、
    # 計測のたびに読むには大きすぎ、問題の対象でもないもの。このファイルの無い写しの指紋は変わらない
    skip = set()
    ex = os.path.join(root, '.snapshot-exclude')
    if os.path.exists(ex):
        with open(ex, encoding='utf-8') as f:
            skip = {os.path.normcase(os.path.join(root, x.strip())) for x in f if x.strip() and not x.startswith('#')}
    files = []
    for d, dirs, names in os.walk(root):
        dirs[:] = sorted(x for x in dirs if not x.startswith('.') and os.path.normcase(os.path.join(d, x)) not in skip)
        files += [os.path.join(d, x) for x in names if not x.startswith('.')]
    h = hashlib.sha256()
    for fp in sorted(files, key=lambda p: os.path.relpath(p, root).replace('\\', '/').lower()):
        with open(fp, 'rb') as f:
            c = hashlib.sha256(f.read()).hexdigest()
        h.update(f'{os.path.relpath(fp, root).replace(chr(92), "/")}\t{c}\n'.encode('utf-8'))
    print(f'{h.hexdigest()}  ({len(files)} files)')


def main(argv):
    if len(argv) == 4 and argv[0] == 'rebase':
        rebase(*argv[1:])
    elif len(argv) == 2 and argv[0] == 'digest':
        digest(argv[1])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
