"""ltq の回帰テスト(合成した小さな索引で、構成の統合と鮮度の扱いを確かめる)。

usage: python -I -m unittest discover -s query/test   (リポジトリのルートで)
"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import ltq  # noqa: E402

A_CPP = """\
void Run();
void Main() {
  Run();
#ifdef DEBUG_ONLY
  Run();
#endif
  // Run(); in a comment
}
"""


class LtqTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, 'src'))
        os.makedirs(os.path.join(self.root, '.index'))
        self.t0 = time.time() - 1000
        self.write('src/a.cpp', A_CPP, self.t0 - 10)

    def tearDown(self):
        shutil.rmtree(self.root)

    def write(self, rel, text, mtime=None):
        fp = os.path.join(self.root, rel)
        with open(fp, 'w', encoding='utf-8', newline='\n') as f:
            f.write(text)
        if mtime is not None:
            os.utime(fp, (mtime, mtime))

    def index(self, name, rows, skips, tus=('src/a.cpp',), mtime=None):
        base = os.path.join(self.root, '.index', name + '.tsv')
        with open(base, 'w', encoding='utf-8', newline='\n') as f:
            for sym, role, caller, loc, src in rows:
                f.write(f'{sym}\t{role}\t{caller}\t{loc}\t{src}\n')
        with open(base + '.skip.tsv', 'w', encoding='utf-8', newline='\n') as f:
            f.write('path\tkind\tbegin\tend\n')
            for path, kind, b, e in skips:
                f.write(f'{path}\t{kind}\t{b}\t{e}\n')
        with open(base + '.tu.tsv', 'w', encoding='utf-8', newline='\n') as f:
            f.write('file\tok\terrors\tsec\trefs\tfirst_error\n')
            for t in tus:
                f.write(f'{os.path.join(self.root, t)}\t1\t0\t0.1\t1\t\n')
        t = self.t0 if mtime is None else mtime
        for p in (base, base + '.skip.tsv', base + '.tu.tsv'):
            os.utime(p, (t, t))

    def ltq(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ltq.main(['--root', self.root] + list(args))
        self.assertEqual(code, 0, out.getvalue())
        return out.getvalue()

    # 共通の行: 3 行目の呼び出しと定義の宣言
    COMMON = [('Run', 'decl', '', 'src/a.cpp:1:6', 'void Run();'),
              ('Run', 'call', 'Main', 'src/a.cpp:3:3', 'Run();')]
    COMMENT = ('src/a.cpp', 'comment', '7:3', '7:24')

    def two_configs(self):
        # debug は 5 行目を解析し、release では 4〜6 行目が無効な #if
        self.index('index', self.COMMON + [('Run', 'call', 'Main', 'src/a.cpp:5:3', 'Run();')], [self.COMMENT])
        self.index('release', self.COMMON, [self.COMMENT, ('src/a.cpp', 'inactive', '4:1', '6:7')])

    def test_single_index_unchanged_header(self):
        self.index('index', self.COMMON, [self.COMMENT])
        out = self.ltq('refs', 'Run')
        self.assertIn('index: .index/index.tsv (built', out)
        self.assertNotIn('configurations', out)
        self.assertNotIn('only in', out)
        self.assertIn('src/a.cpp:3  [in Main]  | Run();', out)
        # 5 行目は索引に無く、無効な #if の範囲も無いので「その他」
        self.assertIn('other -> not explained by the index', out)

    def test_merge_marks_rows_only_in_some_configs(self):
        self.two_configs()
        out = self.ltq('refs', 'Run')
        self.assertIn('index: 2 configurations, merged', out)
        self.assertIn('src/a.cpp:3  [in Main]  | Run();', out)  # 両方にある行は印なし
        self.assertIn('src/a.cpp:5  [in Main]  [only in index]  | Run();', out)
        self.assertIn('== calls (resolved by clang): 2', out)
        self.assertIn('inside comments', out)

    def test_inactive_only_when_inactive_in_every_config(self):
        # 5 行目がどちらの索引にも無い。release だけで無効なら inactive とは言えない
        self.index('index', self.COMMON, [self.COMMENT])
        self.index('release', self.COMMON, [self.COMMENT, ('src/a.cpp', 'inactive', '4:1', '6:7')])
        out = self.ltq('refs', 'Run')
        self.assertNotIn('inside inactive #if', out)
        self.assertIn('other -> not explained by the index', out)
        # 両方で無効なら inactive
        self.index('index', self.COMMON, [self.COMMENT, ('src/a.cpp', 'inactive', '4:1', '6:7')])
        out = self.ltq('refs', 'Run')
        self.assertIn('inside inactive #if/#ifdef branches', out)

    def test_relocate_after_edit(self):
        self.index('index', self.COMMON + [('Run', 'call', 'Main', 'src/a.cpp:5:3', 'Run();')], [self.COMMENT])
        # 先頭に 2 行足し、5 行目(今は 7 行目)を書き換える
        edited = '// x\n// y\n' + A_CPP.replace('#ifdef DEBUG_ONLY\n  Run();', '#ifdef DEBUG_ONLY\n  Run(); // edited')
        self.write('src/a.cpp', edited)
        out = self.ltq('refs', 'Run')
        self.assertIn('1 source files changed after indexing', out)
        self.assertIn('src/a.cpp:5  [in Main]  [moved from line 3]', out)
        self.assertIn('src/a.cpp:7  [in Main]  [line changed or removed after indexing; estimated from line 5]', out)
        self.assertIn('== calls (resolved by clang): 2', out)  # 同じ文字列の 2 行を 1 行にまとめない
        # コメントの範囲も 2 行ずれる(7 行目のコードをコメントと誤らない)
        self.assertIn('inside comments -> not code; do not report these: 1\n'
                      '    src/a.cpp:9  [file changed after indexing]  | // Run(); in a comment', out)
        self.assertNotIn('other ->', out)
        out = self.ltq('check', 'src/a.cpp:5', 'src/a.cpp:9')
        self.assertIn('indexed: call Run at col 3  [in Main]  [moved from line 3]', out)
        self.assertIn('inside comment 9:3-9:24  (shifted with the nearby indexed lines)', out)

    def test_block_deletion(self):
        # 3 つの関数が同じ文字列の行を持つ。先頭の関数を消しても、残りの行を取り違えない
        body = ''.join(f'void {f}() {{\n  Run();\n  x = 1;\n}}\n' for f in 'ABC')
        self.write('src/b.cpp', body, self.t0 - 10)
        rows = []
        for i, f in enumerate('ABC'):
            rows += [(f, 'def', '', f'src/b.cpp:{4 * i + 1}:6', f'void {f}() {{'),
                     ('Run', 'call', f, f'src/b.cpp:{4 * i + 2}:3', 'Run();')]
        self.index('index', rows, [], tus=('src/b.cpp',))
        self.write('src/b.cpp', body.split('}\n', 1)[1])
        out = self.ltq('refs', 'Run')
        self.assertIn('src/b.cpp:2  [in B]  [moved from line 6]', out)
        self.assertIn('src/b.cpp:6  [in C]  [moved from line 10]', out)
        self.assertRegex(out, r'src/b.cpp:\d+  \[in A\]  \[line changed or removed after indexing')

    def test_header_without_comments_is_tracked(self):
        # コメントが無く TU の主ファイルでもないヘッダー(索引の行だけがある)も、変わったファイルとして扱う
        self.write('src/h.h', 'void Run();\n', self.t0 - 10)
        self.index('index', self.COMMON + [('Run', 'decl', '', 'src/h.h:1:6', 'void Run();')], [self.COMMENT])
        self.write('src/h.h', '#pragma once\nvoid Run();\n')
        out = self.ltq('refs', 'Run')
        self.assertIn('    src/h.h\n', out)
        self.assertIn('src/h.h:2  [moved from line 1]  | void Run();', out)
        self.assertNotIn('not read by any indexed configuration', out)

    def test_newer_config_wins(self):
        # debug は編集前、release は編集後に作り直した。debug の推定と release の確かな行が同じ参照なら release を採る
        self.write('src/n.cpp', 'void Main() {\n  Go();\n}\n', self.t0 - 10)
        self.index('index', [('Main', 'def', '', 'src/n.cpp:1:6', 'void Main() {'),
                             ('Go', 'call', 'Main', 'src/n.cpp:2:3', 'Go();')], [], tus=('src/n.cpp',))
        self.write('src/n.cpp', '// top\nvoid Main() {\n  Go(); // now\n}\n')
        self.index('release', [('Main', 'def', '', 'src/n.cpp:2:6', 'void Main() {'),
                               ('Go', 'call', 'Main', 'src/n.cpp:3:3', 'Go(); // now')], [],
                   tus=('src/n.cpp',), mtime=time.time() + 10)
        out = self.ltq('refs', 'Go')
        self.assertIn('== calls (resolved by clang): 1\n  call   src/n.cpp:3  [in Main]  | Go(); // now', out)
        # 推定した行が別の行に落ちても、release に同じ参照があれば捨てる
        self.index('index', [('Main', 'def', '', 'src/n.cpp:1:6', 'void Main() {'),
                             ('Go', 'call', 'Main', 'src/n.cpp:5:3', 'Go();')], [], tus=('src/n.cpp',))
        out = self.ltq('refs', 'Go')
        self.assertIn('== calls (resolved by clang): 1\n', out)

    def test_new_and_deleted_files(self):
        self.write('src/b.cpp', 'void Run() {}\n', self.t0 - 10)
        self.index('index', self.COMMON + [('Run', 'def', '', 'src/b.cpp:1:6', 'void Run() {}')], [self.COMMENT],
                   tus=('src/a.cpp', 'src/b.cpp'))
        os.remove(os.path.join(self.root, 'src', 'b.cpp'))
        self.write('src/c.cpp', 'void Other() {}\n')
        out = self.ltq('refs', 'Run')
        self.assertIn('1 source files modified after indexing are not read by any indexed configuration', out)
        self.assertIn('src/c.cpp', out)
        self.assertIn('1 indexed TU files no longer exist: src/b.cpp', out)
        self.assertIn('src/b.cpp:1  [file deleted after indexing]', out)

    def test_explicit_index_option_is_repeatable(self):
        self.two_configs()
        d = os.path.join(self.root, '.index')
        out = self.ltq('--index', os.path.join(d, 'release.tsv'), 'refs', 'Run')
        self.assertIn('index: .index/release.tsv', out)
        self.assertNotIn('only in', out)
        out = self.ltq('--index', os.path.join(d, 'index.tsv'), '--index', os.path.join(d, 'release.tsv'), 'refs', 'Run')
        self.assertIn('[only in index]', out)


if __name__ == '__main__':
    unittest.main()
