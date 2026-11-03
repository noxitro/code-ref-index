"""段階 2 の問い合わせ CLI: ltidx の索引(TSV)を引き、ソースのテキスト一致と突き合わせる。

  ltq refs <名前>            参照・定義・dep 候補と、索引に無いテキスト一致の理由
  ltq check <path:line>...   その行が索引でどう扱われているか(参照・コメント・無効な #if)

エージェントが読む前提なので、出力の見出しは英語(短く、文字化けしない)。
"""
import argparse
import bisect
import os
import re
import sys
import time

SRC_EXT = {'.h', '.hh', '.hpp', '.hxx', '.c', '.cc', '.cpp', '.cxx', '.inl', '.ipp', '.inc', '.ixx'}
DEFS = ('def', 'decl')


class Index:
    def __init__(self, path, root):
        self.path = path
        self.root = os.path.abspath(root)
        self.mtime = os.path.getmtime(path)

    def rows(self, keep):
        """keep(bytes の行) が真の行だけを (sym, role, caller, path, line, col, src) にして返す。"""
        out = []
        with open(self.path, 'rb') as f:
            for raw in f:
                if not keep(raw):
                    continue
                p = raw.rstrip(b'\r\n').split(b'\t', 4)
                if len(p) < 5:
                    continue
                loc = p[3].rsplit(b':', 2)
                out.append((p[0].decode('utf-8', 'replace'), p[1].decode(), p[2].decode('utf-8', 'replace'),
                            loc[0].decode('utf-8', 'replace'), int(loc[1]), int(loc[2]),
                            p[4].decode('utf-8', 'replace')))
        return out

    def skips(self, paths):
        """paths のファイルについて、コメントと無効な #if の範囲を {path: [(kind, (l,c), (l,c))]} で返す。"""
        res = {}
        sp = self.path + '.skip.tsv'
        if not os.path.exists(sp):
            return res
        want = {p.encode('utf-8') for p in paths}
        with open(sp, 'rb') as f:
            next(f, None)
            for raw in f:
                p = raw.rstrip(b'\r\n').split(b'\t')
                if len(p) != 4 or p[0] not in want:
                    continue
                b, e = (tuple(int(x) for x in s.split(b':')) for s in p[2:4])
                res.setdefault(p[0].decode('utf-8'), []).append((p[1].decode(), b, e))
        return res

    def tu_status(self):
        """(TU 数, [(エラーのあった TU, 最初のエラー)], TU の主ファイルの集合)"""
        tp = self.path + '.tu.tsv'
        total, bad, files = 0, [], set()
        if os.path.exists(tp):
            with open(tp, encoding='utf-8', errors='replace') as f:
                next(f, None)
                for line in f:
                    c = line.rstrip('\n').split('\t')
                    if len(c) < 2:
                        continue
                    total += 1
                    files.add(self.rel(c[0]))
                    if c[1] != '1':
                        bad.append((self.rel(c[0]), c[5] if len(c) > 5 else ''))
        return total, bad, files

    def rel(self, p):
        a = os.path.abspath(p)
        if a.lower().startswith(self.root.lower() + os.sep):
            a = a[len(self.root) + 1:]
        return a.replace('\\', '/')

    def header(self):
        total, bad, _ = self.tu_status()
        built = time.strftime('%Y-%m-%d %H:%M', time.localtime(self.mtime))
        print(f'index: {self.rel(self.path)} (built {built}; TU {total - len(bad)}/{total} without errors)')
        if bad:
            print(f'  {len(bad)} TU had parse errors; references only in those TUs may be missing:')
            for p, e in bad:
                print(f'    {p}  ({e})')


def in_range(pos, b, e):
    return b <= pos < e


def classify_ranges(ranges, line, col):
    """(line, col) を含む範囲の (種類, 範囲の表示)。コメントを優先する。"""
    hit = [(k, b, e) for k, b, e in ranges if in_range((line, col), b, e) or (k == 'inactive' and b[0] <= line <= e[0])]
    hit.sort(key=lambda x: x[0] != 'comment')
    if not hit:
        return None, ''
    k, b, e = hit[0]
    return k, (f'  [lines {b[0]}-{e[0]}]' if b[0] != e[0] else '')


def walk_sources(idx, excludes):
    """excludes はディレクトリ名(どこにあっても除く)か、ルートからの相対パスの接頭辞。"""
    pre = tuple(e.strip('/') + '/' for e in excludes if '/' in e)
    for d, dirs, files in os.walk(idx.root):
        dirs[:] = [x for x in dirs if not x.startswith('.') and x not in excludes]
        if pre and (idx.rel(d) + '/').startswith(pre):
            dirs[:] = []
            continue
        for fn in files:
            if os.path.splitext(fn)[1].lower() in SRC_EXT:
                yield os.path.join(d, fn)


def lexical_hint(data, starts, ln, col):
    """索引に無い一致の見当: 文字列リテラルの中か、マクロ定義の中か。"""
    b = starts[ln - 1]
    before = data[b:b + col - 1]
    if len(re.findall(rb'(?<!\\)"', before)) % 2 == 1:
        return 'string'
    i = ln - 1
    while i > 0 and data[starts[i - 1]:starts[i]].rstrip(b'\r\n').endswith(b'\\'):
        i -= 1
    if re.match(rb'\s*#\s*define\b', data[starts[i]:]):
        return 'macro'
    return None


def text_hits(idx, short, excludes):
    """ソース中の単語一致を (path, line, col, src, stale, hint) で返す。col は clang と同じ 1 始まりのバイト位置。"""
    sb = short.encode('utf-8')
    pat = re.compile(rb'(?<![A-Za-z0-9_])' + re.escape(sb) + rb'(?![A-Za-z0-9_])')
    hits = []
    for fp in walk_sources(idx, excludes):
        try:
            data = open(fp, 'rb').read()
        except OSError:
            continue
        if sb not in data:
            continue
        rel = idx.rel(fp)
        stale = os.path.getmtime(fp) > idx.mtime
        starts = None
        for m in pat.finditer(data):
            if starts is None:
                starts = [0] + [i + 1 for i, ch in enumerate(data) if ch == 10]
            ln = bisect.bisect_right(starts, m.start())
            b = starts[ln - 1]
            e = data.find(b'\n', m.start())
            src = data[b:e if e >= 0 else len(data)].strip().decode('utf-8', 'replace')[:200]
            col = m.start() - b + 1
            hits.append((rel, ln, col, src, stale, lexical_hint(data, starts, ln, col)))
    return hits


def by_dir(items):
    """[(path, 行の表示)] をディレクトリごとの件数にまとめる。"""
    agg = {}
    for path, _ in items:
        d = path.rsplit('/', 1)[0] if '/' in path else '.'
        n, files = agg.get(d, (0, set()))
        files.add(path)
        agg[d] = (n + 1, files)
    return [f'{d}/  {n} matches in {len(fs)} files' for d, (n, fs) in sorted(agg.items())]


def fmt(r, show_sym):
    sym, role, caller, path, line, col, src = r
    s = f'{role:<6} {path}:{line}'
    if show_sym:
        s += f'  {sym}'
    if caller:
        s += f'  [in {caller}]'
    return s + f'  | {src}'


def section(title, items, limit):
    print(f'\n== {title}: {len(items)}')
    for s in items[:limit]:
        print('  ' + s)
    if len(items) > limit:
        print(f'  ... {len(items) - limit} more (raise --limit)')


def cmd_refs(a, idx):
    name = a.name.lstrip(':')
    short = re.split(r'::', name)[-1]
    sb = short.encode('utf-8')

    def match(sym):
        return sym == name or sym.endswith('::' + name)

    rows = [r for r in idx.rows(lambda raw: sb in raw) if r[0].rsplit('::', 1)[-1] == short]
    target = [r for r in rows if match(r[0])]
    deps = [r for r in rows if r[0] == '?::' + short]
    syms = sorted({r[0] for r in target})
    show_sym = len(syms) > 1

    idx.header()
    print(f'query: {a.name}')
    if not syms:
        print('  no symbol in the index matches this name.')
    elif show_sym:
        print('  matched symbols: ' + ', '.join(f'{s} ({sum(1 for r in target if r[0] == s)})' for s in syms))
    else:
        print(f'  symbol: {syms[0]}')

    key = lambda r: (r[3], r[4], r[5], r[1].endswith('-using'))
    # 同じ位置に直接の解決と using 越しの解決があれば、直接のほうだけ残す
    seen = set()
    uniq = []
    for r in sorted(target, key=key):
        if r[1].endswith('-using') and (r[0], r[3], r[4], r[5]) in seen:
            continue
        seen.add((r[0], r[3], r[4], r[5]))
        uniq.append(r)
    target = uniq
    calls = [r for r in target if r[1].startswith('call')]
    uses = sorted((r for r in target if not r[1].startswith(('call',) + DEFS)), key=key)
    defs = sorted((r for r in target if r[1].startswith(DEFS)), key=key)
    section('calls (resolved by clang)', [fmt(r, show_sym) for r in calls], a.limit)
    if any(r[1].endswith('-using') for r in target):
        print('  (call-using: a call through a using-declaration inside a template, resolved from its instantiations)')
    section('other references (address taken, template arguments, writes, ...)',
            [fmt(r, show_sym) for r in uses], a.limit)
    section('definitions and declarations', [fmt(r, show_sym) for r in defs], a.limit)

    # dep 行: テンプレート本体では未解決。実体化で解決された位置は、その解決先を添える
    at = {}
    for r in rows:
        at.setdefault((r[3], r[4]), []).append(r)
    target_pos = {(r[3], r[4]) for r in target}
    def_files = {r[3] for r in defs}
    dep_lines, dep_dup = [], 0
    for r in sorted(deps, key=key):
        if (r[3], r[4]) in target_pos:
            dep_dup += 1  # 実体化で対象に解決済み。上の calls に載っている
            continue
        resolved = sorted({x[0] for x in at[(r[3], r[4])] if x[0] != r[0] and not x[1].startswith(DEFS)})
        if resolved:
            mark = '  (instantiations resolve it to ' + ', '.join(resolved) + ' -> probably not the query)'
        else:
            mark = '  (never instantiated; unknown target' + (', same file as the definition)' if r[3] in def_files else ')')
        dep_lines.append(fmt(r, False) + mark)
    print(f'\n== unresolved template calls named "{short}" (dep): {len(dep_lines)}'
          + (f' (+{dep_dup} already listed in calls)' if dep_dup else ''))
    if dep_lines:
        print('  clang could not resolve these inside templates. Decide from the source line and the')
        print('  enclosing function whether each one calls the queried symbol; they are not counted above.')
    for s in dep_lines[:a.limit]:
        print('  ' + s)
    if len(dep_lines) > a.limit:
        print(f'  ... {len(dep_lines) - a.limit} more (raise --limit)')

    # テキスト一致のうち、索引に同名の行が無いものの理由
    hits = text_hits(idx, short, set(a.exclude))
    known = {r[3] for r in rows}
    loose = [h for h in hits if (h[0], h[1]) not in at]
    ranges = idx.skips({h[0] for h in loose})
    parsed = known | set(ranges) | idx.tu_status()[2]
    tagged = {r[0] for r in target} | {'?::' + short}
    others = {}
    for r in rows:
        if r[0] not in tagged:
            others[r[0]] = others.get(r[0], 0) + 1
    cats = {k: [] for k in ('comment', 'inactive', 'unparsed', 'string', 'macro', 'other')}
    for path, line, col, src, stale, hint in loose:
        k, span = classify_ranges(ranges.get(path, []), line, col)
        if k is None:
            k = (hint or 'other') if path in parsed else 'unparsed'
        cats[k].append((path, f'{path}:{line}{span}{"  [file changed after indexing]" if stale else ""}  | {src}'))
    stale_hits = sorted({f'{h[0]}:{h[1]}' for h in hits if h[4] and (h[0], h[1]) in at})

    print(f'\n== text matches of "{short}" not in the index: '
          f'{sum(len(v) for v in cats.values())} of {len(hits)} word matches')
    groups = [
        ('comment', 'inside comments -> not code; do not report these', False),
        ('inactive', 'inside inactive #if/#ifdef branches -> not compiled in the indexed configuration', False),
        ('macro', 'inside macro definitions -> expansions are indexed at the use site', False),
        ('string', 'inside string literals -> not code', True),
        ('unparsed', 'in files no indexed TU includes -> not analyzed; read them if they matter', True),
        ('other', 'other -> not explained by the index; read them if they matter', False),
    ]
    for k, title, fold in groups:
        items = cats[k]
        if not items:
            continue
        print(f'  {title}: {len(items)}')
        lines = by_dir(items) if fold and len(items) > 10 else [s for _, s in items]
        for s in lines[:a.limit]:
            print('    ' + s)
        if len(lines) > a.limit:
            print(f'    ... {len(lines) - a.limit} more (raise --limit)')
    if stale_hits:
        print(f'  note: {len(stale_hits)} indexed matches are in files changed after indexing; line numbers may be off')
    if others:
        top = sorted(others.items(), key=lambda kv: -kv[1])
        print(f'\n== other symbols named "{short}" (not the query): ' +
              ', '.join(f'{s} ({n})' for s, n in top[:15]) + (' ...' if len(top) > 15 else ''))


def cmd_check(a, idx):
    locs = []
    for s in a.locs:
        m = re.match(r'^(.*?):(\d+)(?::(\d+))?$', s.replace('\\', '/'))
        if not m:
            print(f'{s}: expected path:line')
            continue
        p = m.group(1)[2:] if m.group(1).startswith('./') else m.group(1)
        locs.append((p, int(m.group(2)), int(m.group(3)) if m.group(3) else None))
    paths = {p for p, _, _ in locs}
    pb = [p.encode('utf-8') for p in paths]
    rows = idx.rows(lambda raw: any(b'\t' + p + b':' in raw for p in pb))
    ranges = idx.skips(paths)
    idx.header()
    for path, line, col in locs:
        here = [r for r in rows if r[3] == path and r[4] == line and (col is None or r[5] == col)]
        fp = os.path.join(idx.root, path)
        tag = ''
        if not os.path.exists(fp):
            tag = '  [file not found]'
        elif os.path.getmtime(fp) > idx.mtime:
            tag = '  [file changed after indexing]'
        print(f'\n{path}:{line}{":" + str(col) if col else ""}{tag}')
        for r in sorted(here, key=lambda r: r[5]):
            print(f'  indexed: {r[1]} {r[0]} at col {r[5]}' + (f'  [in {r[2]}]' if r[2] else ''))
        for k, b, e in ranges.get(path, []):
            if (col is not None and in_range((line, col), b, e)) or (col is None and b[0] <= line <= e[0]):
                what = 'comment' if k == 'comment' else 'inactive #if branch'
                print(f'  inside {what} {b[0]}:{b[1]}-{e[0]}:{e[1]}')
        if not here and path not in ranges and not any(r[3] == path for r in rows):
            print('  file not seen by any indexed TU')
        elif not here:
            print('  no indexed reference on this line')


def find_index(root):
    """<root>/.index/index.tsv。無ければ .index の中の索引本体(.tu.tsv と .skip.tsv 以外の .tsv)が 1 つならそれ。"""
    d = os.path.join(root, '.index')
    p = os.path.join(d, 'index.tsv')
    if os.path.exists(p) or not os.path.isdir(d):
        return p
    main = [f for f in os.listdir(d) if f.endswith('.tsv') and not f.endswith(('.tu.tsv', '.skip.tsv'))]
    return os.path.join(d, main[0]) if len(main) == 1 else p


def main(argv=None):
    """戻り値は終了コード。結果は標準出力に書く(MCP サーバーはこれを取り込んで返す)。"""
    ap = argparse.ArgumentParser(prog='ltq', description='C++ reference index query (ltidx TSV)')
    ap.add_argument('--index', help='index TSV (default <root>/.index/index.tsv, or the only index in <root>/.index)')
    ap.add_argument('--root', default='.', help='source root the index paths are relative to (default .)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('refs', help='references, definitions, unresolved template calls, and why text matches are missing')
    r.add_argument('name', help='qualified name or its suffix, e.g. nox::io::File::Read or File::Read')
    r.add_argument('--limit', type=int, default=200, help='max lines per section (default 200)')
    r.add_argument('--exclude', action='append', default=[], help='directory name to skip in the text search')
    c = sub.add_parser('check', help='how the index treats each path:line[:col]')
    c.add_argument('locs', nargs='+')
    a = ap.parse_args(argv)
    index = a.index or find_index(a.root)
    if not os.path.exists(index):
        print(f'index not found: {os.path.abspath(index)}  (build it with ltidx, or pass --index)')
        return 2
    idx = Index(index, a.root)
    (cmd_refs if a.cmd == 'refs' else cmd_check)(a, idx)
    return 0


if __name__ == '__main__':
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
