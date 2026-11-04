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

SRC_EXT = {'.h', '.hh', '.hpp', '.hxx', '.c', '.cc', '.cpp', '.cxx', '.inl', '.ipp', '.inc', '.ixx', '.cppm', '.tpp', '.txx',
           '.m', '.mm'}
TU_EXT = {'.c', '.cc', '.cpp', '.cxx', '.cppm', '.ixx', '.m', '.mm'}  # 単独でコンパイルされうるもの
DEFS = ('def', 'decl')


def longest_increasing(pairs):
    """(old, new) の並び(old の昇順)から、new も増えていく最長の部分列。"""
    tails, vals, prev = [], [], [None] * len(pairs)  # tails[k]: 長さ k+1 の列の末尾の添え字、vals[k]: その new
    for i, (_, n) in enumerate(pairs):
        k = bisect.bisect_left(vals, n)
        prev[i] = tails[k - 1] if k else None
        if k == len(tails):
            tails.append(i)
            vals.append(n)
        else:
            tails[k], vals[k] = i, n
    out, i = [], tails[-1] if tails else None
    while i is not None:
        out.append(pairs[i])
        i = prev[i]
    return out[::-1]


def canon_line(raw):
    """ltidx が索引に入れるソース行と同じ形(前後の空白を除き 200 バイトまで、タブと改行は空白)。"""
    s = raw.strip()[:200]
    return s.translate(bytes.maketrans(b'\t\r\n', b'   ')).decode('utf-8', 'replace')


class Part:
    """1 つの構成(Debug、Release など)の索引。名前はファイル名の拡張子を除いたもの。"""

    def __init__(self, path):
        self.path = path
        self.name = os.path.splitext(os.path.basename(path))[0]
        self.mtime = os.path.getmtime(path)


class Index:
    """1 つ以上の構成の索引をまとめて引く。行は和集合にし、一部の構成にしか無い行には構成名を付ける。"""

    def __init__(self, paths, root):
        self.root = os.path.abspath(root)
        self.parts = [Part(p) for p in paths]
        self.mtime = min(p.mtime for p in self.parts)  # 鮮度は最も古い索引で判定する
        self._seen = None
        self._fresh = None
        self._lines = {}
        self._maps = {}  # (構成名, path) -> 行の付け直しの対応。rows() が作り、skips() も使う

    def _current(self, path):
        if path not in self._lines:
            try:
                with open(os.path.join(self.root, path), 'rb') as f:
                    data = f.read()
            except OSError:
                data = None
            self._lines[path] = None if data is None else [canon_line(x) for x in data.split(b'\n')]
        return self._lines[path]

    def _stale(self, part):
        """その索引の後に変わった(か消えた)、索引にあるファイル。"""
        out = set()
        for path in self.freshness()[0]:
            try:
                if os.path.getmtime(os.path.join(self.root, path)) > part.mtime:
                    out.add(path)
            except OSError:
                out.add(path)
        return out | {p for p in self.seen()[part.name] if not os.path.exists(os.path.join(self.root, p))}

    def _line_map(self, path, anchors):
        """索引にある (行, ソース行) を今のファイルから探し、{索引の行: 今の行 か None}。
        1) 索引でも今のファイルでも 1 回しか現れない行を対応づけ、今の行番号が増えていく最長の並びだけを固定する
           (行の入れ替えで食い違う対応は捨てる)。
        2) 残りの行(`Run();` のように何度も現れる行など)は、前後の固定した行の間だけを、
           直前に対応づけた行のずれの近くから探す。1 つの行に 2 つを割り当てない。"""
        old = {}
        for line, src in anchors:
            old.setdefault(line, src)
        lines = sorted(old)
        cur = self._current(path)
        if cur is None:
            return {line: None for line in lines}
        pos = {}
        for i, s in enumerate(cur, 1):
            pos.setdefault(s, []).append(i)
        count = {}
        for s in old.values():
            count[s] = count.get(s, 0) + 1
        pairs = [(o, pos[old[o]][0]) for o in lines if count[old[o]] == 1 and len(pos.get(old[o], ())) == 1]
        res = dict(longest_increasing(pairs))
        fixed = sorted(res)
        prev_o, prev_n = 0, 0
        for o in lines:
            if o in res:
                prev_o, prev_n = o, res[o]
                continue
            i = bisect.bisect_right(fixed, o)
            hi = res[fixed[i]] if i < len(fixed) else len(cur) + 1
            cand = pos.get(old[o], [])
            want = o + (prev_n - prev_o)
            j = bisect.bisect_left(cand, want)
            best = None
            for n in (cand[j - 1] if j > 0 else None, cand[j] if j < len(cand) else None):
                if n is not None and prev_n < n < hi and (best is None or abs(n - want) < abs(best - want)):
                    best = n
            if best is None and j < len(cand) and cand[j] <= prev_n:  # want が直前の行より前にある
                k = bisect.bisect_right(cand, prev_n)
                best = cand[k] if k < len(cand) and cand[k] < hi else None
            res[o] = best
            if best is not None:
                prev_o, prev_n = o, best
        return res

    def _set_map(self, part, path, lmap):
        known = sorted((o, n - o) for o, n in lmap.items() if n is not None)
        self._maps[(part.name, path)] = (lmap, [o for o, _ in known], [d for _, d in known])

    def shift_at(self, part, path, line):
        """変わったファイルで、索引の line 行が今どれだけずれているか(最も近い、対応のついた行から)。"""
        m = self._maps.get((part.name, path))
        if not m or not m[1]:
            return 0
        olds, shifts = m[1], m[2]
        i = bisect.bisect_left(olds, line)
        near = [k for k in (i - 1, i) if 0 <= k < len(olds)]
        return shifts[min(near, key=lambda k: abs(olds[k] - line))]

    def _relocate(self, part, path, line):
        """(今の行番号, 注記)。行が見つからなければ、近くの変わっていない行のずれから推定する。"""
        cur = self._current(path)
        if cur is None:
            return line, 'file deleted after indexing'
        new = self._maps[(part.name, path)][0].get(line)
        if new is not None:
            return new, ('' if new == line else f'moved from line {line}')
        shift = self.shift_at(part, path, line)
        est = min(max(line + shift, 1), max(len(cur), 1))
        return est, 'line changed or removed after indexing' + (f'; estimated from line {line}' if est != line else '')

    def rows(self, keep):
        """keep(bytes の行) が真の行だけを (sym, role, caller, path, line, col, src, note, configs) にして返す。
        note は行の付け直しの注記、configs はその行がある構成名の frozenset。
        索引の後に変わったファイルの行は、索引にあるソース行の文字列で今のファイルから探し直す。"""
        merged = {}
        for part in self.parts:
            stale = self._stale(part)
            sb = {p.encode('utf-8') for p in stale}
            anchors, picked = {}, []
            with open(part.path, 'rb') as f:
                for raw in f:
                    hit = keep(raw)
                    if not hit:  # 変わったファイルの行は、行の付け直しの手がかりとして読む
                        if not sb:
                            continue
                        q = raw.split(b'\t', 4)
                        if len(q) < 5 or q[3].rsplit(b':', 2)[0] not in sb:
                            continue
                    p = raw.rstrip(b'\r\n').split(b'\t', 4)
                    if len(p) < 5:
                        continue
                    loc = p[3].rsplit(b':', 2)
                    path, src = loc[0].decode('utf-8', 'replace'), p[4].decode('utf-8', 'replace')
                    if path in stale:
                        anchors.setdefault(path, []).append((int(loc[1]), src))
                    if hit:
                        picked.append((p, path, int(loc[1]), int(loc[2]), src))
            for path, a in anchors.items():
                self._set_map(part, path, self._line_map(path, a))
            for p, path, line, col, src in picked:
                note = ''
                if path in stale:
                    line, note = self._relocate(part, path, line)
                k = (p[0].decode('utf-8', 'replace'), p[1].decode(), p[2].decode('utf-8', 'replace'), path, line, col)
                if k in merged:
                    merged[k][2].add(part.name)
                    if merged[k][1] and not note:  # 付け直しの要らない(新しい)構成のソース行を採る
                        merged[k][0:2] = [src, note]
                else:
                    merged[k] = [src, note, {part.name}]
        # 古い構成で行を推定した参照は、別の構成に同じ参照(行以外が同じ)の確かな行があれば捨てる
        exact = {}
        for k, v in merged.items():
            if not v[1].startswith('line changed'):
                exact.setdefault(k[:4] + (k[5],), set()).update(v[2])
        return [k + (v[0], v[1], frozenset(v[2])) for k, v in merged.items()
                if not (v[1].startswith('line changed') and exact.get(k[:4] + (k[5],), set()) - v[2])]

    def skips(self, paths):
        """paths のファイルについて、コメントと無効な #if の範囲を {path: [(kind, (l,c), (l,c), 構成名)]} で返す。
        索引の後に変わったファイルの範囲は、rows() が作った行の対応で近くのずれだけ動かす(rows() の後に呼ぶ)。"""
        res = {}
        want = {p.encode('utf-8') for p in paths}
        for part in self.parts:
            sp = part.path + '.skip.tsv'
            if not os.path.exists(sp):
                continue
            with open(sp, 'rb') as f:
                next(f, None)
                for raw in f:
                    p = raw.rstrip(b'\r\n').split(b'\t')
                    if len(p) != 4 or p[0] not in want:
                        continue
                    b, e = (tuple(int(x) for x in s.split(b':')) for s in p[2:4])
                    path = p[0].decode('utf-8')
                    if (part.name, path) in self._maps:
                        b = (b[0] + self.shift_at(part, path, b[0]), b[1])
                        e = (e[0] + self.shift_at(part, path, e[0]), e[1])
                    res.setdefault(path, []).append((p[1].decode(), b, e, part.name))
        return res

    def seen(self):
        """{構成名: その構成のどれかの TU が読んだファイルの集合}。TU の主ファイル、コメントか無効な #if のあるファイル、
        索引に行のあるファイル。参照もコメントも無いヘッダーは数えられない。"""
        if self._seen is None:
            self._seen = {}
            for part in self.parts:
                s = set(self._tu(part)[2])
                raw_paths = set()
                with open(part.path, 'rb') as f:
                    last = None
                    for raw in f:
                        q = raw.split(b'\t', 4)
                        if len(q) < 5:
                            continue
                        loc = q[3]
                        if last is not None and loc.startswith(last) and loc[len(last):len(last) + 1] == b':':
                            continue  # 同じファイルの行が続く(ltidx は TU ごとにまとめて書く)
                        last = loc.rsplit(b':', 2)[0]
                        raw_paths.add(last)
                sp = part.path + '.skip.tsv'
                if os.path.exists(sp):
                    with open(sp, 'rb') as f:
                        next(f, None)
                        raw_paths.update(raw.split(b'\t', 1)[0] for raw in f)
                s.update(p.decode('utf-8', 'replace') for p in raw_paths)
                self._seen[part.name] = s
        return self._seen

    def partial(self, r):
        """その行が、ファイルを読んだ構成の一部にしか無ければ、ある構成名の並び。全部にあれば ''。"""
        if len(self.parts) < 2:
            return ''
        saw = {n for n, s in self.seen().items() if r[3] in s} | set(r[8])
        return '' if saw <= r[8] else ', '.join(sorted(r[8]))

    def _tu(self, part):
        tp = part.path + '.tu.tsv'
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

    def tu_status(self):
        """(TU 数, [(エラーのあった TU, 最初のエラー, 構成名)], TU の主ファイルの集合)。構成をまたいで足す。"""
        total, bad, files = 0, [], set()
        for part in self.parts:
            t, b, f = self._tu(part)
            total += t
            bad += [(p, e, part.name) for p, e in b]
            files |= f
        return total, bad, files

    def rel(self, p):
        a = os.path.abspath(p)
        if a.lower().startswith(self.root.lower() + os.sep):
            a = a[len(self.root) + 1:]
        return a.replace('\\', '/')

    def freshness(self):
        """索引の後に変わったソースと、どの構成でも読まれていない新しいソース(TU の候補)。"""
        if self._fresh is not None:
            return self._fresh
        changed, new = [], []
        seen = set().union(*self.seen().values())
        for fp in walk_sources(self, set()):
            try:
                m = os.path.getmtime(fp)
            except OSError:
                continue
            if m <= self.mtime:
                continue
            rel = self.rel(fp)
            if rel in seen:
                changed.append(rel)
            elif os.path.splitext(fp)[1].lower() in TU_EXT:
                new.append(rel)
        missing = sorted(p for p in self.tu_status()[2] if not os.path.exists(os.path.join(self.root, p)))
        self._fresh = (sorted(changed), sorted(new), missing)
        return self._fresh

    def header(self):
        def built(part):
            return time.strftime('%Y-%m-%d %H:%M', time.localtime(part.mtime))

        multi = len(self.parts) > 1
        if not multi:
            part = self.parts[0]
            total, bad, _ = self._tu(part)
            print(f'index: {self.rel(part.path)} (built {built(part)}; TU {total - len(bad)}/{total} without errors)')
        else:
            print(f'index: {len(self.parts)} configurations, merged')
            for part in self.parts:
                total, b, _ = self._tu(part)
                print(f'  {part.name}: {self.rel(part.path)} (built {built(part)}; TU {total - len(b)}/{total} without errors)')
            print('  references found only in some of the configurations that read the file are marked [only in ...]')
        bad = {}
        for p, e, n in self.tu_status()[1]:
            bad.setdefault((p, e), []).append(n)
        if bad:
            print(f'  {len(bad)} TU had parse errors; references only in those TUs may be missing:')
            for (p, e), ns in bad.items():
                print(f'    {p}  ({e})' + (f'  [{", ".join(ns)}]' if multi else ''))
        changed, new, missing = self.freshness()
        if changed:
            print(f'  {len(changed)} source files changed after indexing. Indexed lines in them are re-located by their'
                  ' source text ([moved from line N] / [line changed or removed after indexing]); rebuild the index for exact results:')
            for p in changed[:10]:
                print(f'    {p}')
            if len(changed) > 10:
                print(f'    ... {len(changed) - 10} more')
        if new:
            print(f'  {len(new)} source files modified after indexing are not read by any indexed configuration'
                  ' (new files? rebuild the index if they are compiled):')
            for p in new[:10]:
                print(f'    {p}')
            if len(new) > 10:
                print(f'    ... {len(new) - 10} more')
        if missing:
            print(f'  {len(missing)} indexed TU files no longer exist: ' + ', '.join(missing[:10]))


def in_range(pos, b, e):
    return b <= pos < e


def classify_ranges(ranges, line, col, readers):
    """(line, col) を含む範囲の (種類, 範囲の表示)。コメントを優先する。
    無効な #if は、そのファイルを読んだ構成(readers)のすべてで無効なときだけ inactive にする。"""
    hit = [(k, b, e, n) for k, b, e, n in ranges
           if in_range((line, col), b, e) or (k == 'inactive' and b[0] <= line <= e[0])]
    hit.sort(key=lambda x: x[0] != 'comment')
    if hit and hit[0][0] == 'inactive' and not readers <= {n for k, _, _, n in hit if k == 'inactive'}:
        hit = []  # 他の構成ではコンパイルされる
    if not hit:
        return None, ''
    k, b, e, _ = hit[0]
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
            with open(fp, 'rb') as f:
                data = f.read()
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


def fmt(r, show_sym, idx):
    sym, role, caller, path, line, col, src, note, _ = r
    s = f'{role:<6} {path}:{line}'
    if show_sym:
        s += f'  {sym}'
    if caller:
        s += f'  [in {caller}]'
    if note:
        s += f'  [{note}]'
    only = idx.partial(r)
    if only:
        s += f'  [only in {only}]'
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
    section('calls (resolved by clang)', [fmt(r, show_sym, idx) for r in calls], a.limit)
    if calls:
        print('  (exact: clang resolved each call from the real type of its receiver, whatever the variable is named)')
    if any(r[1].endswith('-using') for r in target):
        print('  (call-using: a call through a using-declaration inside a template, resolved from its instantiations)')
    section('other references (address taken, template arguments, writes, ...)',
            [fmt(r, show_sym, idx) for r in uses], a.limit)
    section('definitions and declarations', [fmt(r, show_sym, idx) for r in defs], a.limit)

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
        dep_lines.append(fmt(r, False, idx) + mark)
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
        readers = {n for n, s in idx.seen().items() if path in s}
        k, span = classify_ranges(ranges.get(path, []), line, col, readers)
        if k is None:
            k = (hint or 'other') if path in parsed else 'unparsed'
        cats[k].append((path, f'{path}:{line}{span}{"  [file changed after indexing]" if stale else ""}  | {src}'))

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
        multi = len(idx.parts) > 1
        for r in sorted(here, key=lambda r: r[5]):
            only = idx.partial(r)
            print(f'  indexed: {r[1]} {r[0]} at col {r[5]}' + (f'  [in {r[2]}]' if r[2] else '')
                  + (f'  [{r[7]}]' if r[7] else '') + (f'  [only in {only}]' if only else ''))
        shown = set()
        for k, b, e, n in ranges.get(path, []):
            if (col is not None and in_range((line, col), b, e)) or (col is None and b[0] <= line <= e[0]):
                what = 'comment' if k == 'comment' else 'inactive #if branch'
                s = f'  inside {what} {b[0]}:{b[1]}-{e[0]}:{e[1]}'
                s += f'  [{n}]' if multi and k != 'comment' else ''
                if s not in shown:
                    shown.add(s)
                    print(s + ('  (shifted with the nearby indexed lines)' if tag.endswith('indexing]') else ''))
        if not here and path not in ranges and not any(r[3] == path for r in rows):
            print('  file not seen by any indexed TU')
        elif not here:
            print('  no indexed reference on this line')


def find_indexes(root):
    """<root>/.index の中の索引本体(.tu.tsv と .skip.tsv 以外の .tsv)すべて。構成ごとに 1 つ置く。
    index.tsv を先頭にし、残りは名前順。"""
    d = os.path.join(root, '.index')
    if not os.path.isdir(d):
        return []
    main = sorted((f for f in os.listdir(d) if f.endswith('.tsv') and not f.endswith(('.tu.tsv', '.skip.tsv'))),
                  key=lambda f: (f != 'index.tsv', f))
    return [os.path.join(d, f) for f in main]


def main(argv=None):
    """戻り値は終了コード。結果は標準出力に書く(MCP サーバーはこれを取り込んで返す)。"""
    ap = argparse.ArgumentParser(prog='ltq', description='C++ reference index query (ltidx TSV)')
    ap.add_argument('--index', action='append',
                    help='index TSV; repeat it for several configurations (default: every index in <root>/.index)')
    ap.add_argument('--root', default='.', help='source root the index paths are relative to (default .)')
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('refs', help='references, definitions, unresolved template calls, and why text matches are missing')
    r.add_argument('name', help='qualified name or its suffix, e.g. nox::io::File::Read or File::Read')
    r.add_argument('--limit', type=int, default=200, help='max lines per section (default 200)')
    r.add_argument('--exclude', action='append', default=[], help='directory name to skip in the text search')
    c = sub.add_parser('check', help='how the index treats each path:line[:col]')
    c.add_argument('locs', nargs='+')
    a = ap.parse_args(argv)
    indexes = a.index or find_indexes(a.root)
    missing = [p for p in indexes if not os.path.exists(p)]
    if not indexes or missing:
        where = missing[0] if missing else os.path.join(a.root, '.index', 'index.tsv')
        print(f'index not found: {os.path.abspath(where)}  (build it with ltidx, or pass --index)')
        return 2
    idx = Index(indexes, a.root)
    (cmd_refs if a.cmd == 'refs' else cmd_check)(a, idx)
    return 0


if __name__ == '__main__':
    for s in (sys.stdout, sys.stderr):
        s.reconfigure(encoding='utf-8', errors='replace')
    sys.exit(main())
