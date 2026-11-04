"""ltidx の結合テスト(文字コード・仮想関数の上書き・-update)。一時フォルダに小さなプロジェクトを作って索引にする。

usage: python -I -m unittest discover -s indexer/test   (ltidx をビルドしてから。場所は環境変数 LTIDX で変えられる)
"""
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
LTIDX = os.environ.get('LTIDX') or os.path.join(HERE, '..', 'build', 'ltidx.exe')
sys.path.insert(0, os.path.join(HERE, '..', '..', 'query'))
import ltq  # noqa: E402

SJIS_SRC = '''struct Base { virtual void Draw(); virtual ~Base(); };
struct Derived : Base { void Draw() override; };
void Run();
void Call(Base* b, Derived* d) {
  b->Draw();
  d->Draw();
  // 表
  Run();
  const char* s = "表"; Run();
}
'''


@unittest.skipUnless(os.path.exists(LTIDX), 'ltidx.exe is not built')
class LtidxTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.cmds = {}

    def tearDown(self):
        shutil.rmtree(self.root)

    def write(self, rel, data, args=None):
        """args を渡すと、そのファイルを TU として compile_commands.json に載せる。"""
        fp = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, 'wb') as f:
            f.write(data.encode('utf-8') if isinstance(data, str) else data)
        if args is not None:
            self.cmds[rel] = args
        with open(os.path.join(self.root, 'compile_commands.json'), 'w', encoding='utf-8') as f:
            d = self.root.replace('\\', '/')
            json.dump([{'directory': d, 'file': f'{d}/{r}', 'arguments': ['cl.exe', '/c', '/std:c++17'] + a + [r]}
                       for r, a in self.cmds.items()], f)

    def run_ltidx(self, *args, out='.index/index.tsv'):
        os.makedirs(os.path.join(self.root, '.index'), exist_ok=True)
        r = subprocess.run([LTIDX, '-p', self.root, '-root', self.root, '-o', os.path.join(self.root, out)] + list(args),
                           capture_output=True, text=True, encoding='utf-8', errors='replace')
        return r.returncode, r.stderr

    def rows(self, out='.index/index.tsv'):
        with open(os.path.join(self.root, out), encoding='utf-8') as f:
            return [line.rstrip('\n').split('\t') for line in f]

    def tus(self, out='.index/index.tsv'):
        with open(os.path.join(self.root, out + '.tu.tsv'), encoding='utf-8') as f:
            next(f)
            return {os.path.basename(c[0]): c for c in (line.rstrip('\n').split('\t') for line in f)}

    def has(self, sym, role, loc, out='.index/index.tsv', caller=None):
        return any(r[0] == sym and r[1] == role and r[3] == loc and (caller is None or r[2] == caller)
                   for r in self.rows(out))

    def test_shift_jis(self):
        # 「表」(0x95 0x5C)の 2 バイト目は `\`。UTF-8 として読むと、コメントが次の行を飲み、文字列が閉じない
        self.write('a.cpp', SJIS_SRC.encode('cp932'), [])
        rc, err = self.run_ltidx('-source-cp', '932')
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.has('Run', 'call', 'a.cpp:8:3'))
        self.assertTrue(self.has('Run', 'call', 'a.cpp:9:26'))  # 列は UTF-8 に直した行のバイト位置
        self.assertIn('const char* s = "表"; Run();', [r[4] for r in self.rows()])  # ソース行は UTF-8
        self.assertEqual(self.tus()['a.cpp'][7], '932')
        # ltq も同じ変換をするので、列で突き合わせられる
        out = self.ltq('check', 'a.cpp:9:26', 'a.cpp:7:6')
        self.assertNotIn('configurations', out)
        self.assertIn('indexed: call Run at col 26', out)
        self.assertIn('inside comment 7:3-7:9', out)

    def test_source_charset_flag_wins(self):
        # /source-charset は clang-cl がエラーにするので外し、そのコードページで読む
        self.write('a.cpp', SJIS_SRC.encode('cp932'), ['/source-charset:shift_jis', '/execution-charset:shift_jis'])
        rc, err = self.run_ltidx('-source-cp', '1252')
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.has('Run', 'call', 'a.cpp:8:3'))
        self.assertEqual(self.tus()['a.cpp'][7], '932')

    def test_utf16(self):
        self.write('a.cpp', SJIS_SRC.encode('utf-16'), [])
        rc, err = self.run_ltidx()
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.has('Run', 'call', 'a.cpp:9:26'))

    def test_utf8_unchanged(self):
        self.write('a.cpp', SJIS_SRC, ['/utf-8'])
        rc, err = self.run_ltidx('-source-cp', '932')
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.has('Run', 'call', 'a.cpp:9:26'))
        self.assertEqual(self.tus()['a.cpp'][7], '65001')

    def test_override(self):
        self.write('a.cpp', SJIS_SRC, [])
        rc, err = self.run_ltidx()
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.has('Derived::Draw', 'override', 'a.cpp:2:30', caller='Base::Draw'))
        self.assertTrue(self.has('Base::Draw', 'call', 'a.cpp:5:6'))  # 基底のポインタ越しは基底のメソッドに解決される

    def test_update(self):
        self.write('h.h', '#pragma once\nvoid Shared();\n')
        self.write('a.cpp', '#include "h.h"\nvoid A() { Shared(); }\n', [])
        self.write('b.cpp', 'void Other();\nvoid B() { Other(); }\n', [])
        rc, err = self.run_ltidx()
        self.assertEqual(rc, 0, err)
        with open(os.path.join(self.root, '.index', 'index.tsv.deps.tsv'), encoding='utf-8') as f:
            deps = {(os.path.basename(t), d) for t, d in (line.rstrip('\n').split('\t') for line in list(f)[1:])}
        self.assertEqual(deps, {('a.cpp', 'a.cpp'), ('a.cpp', 'h.h'), ('b.cpp', 'b.cpp')})

        # 何も変えなければ解析しない
        rc, err = self.run_ltidx('-update')
        self.assertIn('update: 0 files changed, 0/2 TU to re-index, 0 TU removed', err)
        self.assertTrue(self.has('Shared', 'call', 'a.cpp:2:12'))

        # ヘッダーを変えると、それを読む TU だけを解析し直す。行がずれた古い行は残らない
        self.touch_later('h.h', '#pragma once\n// moved\nvoid Shared();\n')
        rc, err = self.run_ltidx('-update')
        self.assertIn('update: 1 files changed, 1/2 TU to re-index, 0 TU removed', err)
        self.assertTrue(self.has('Shared', 'decl', 'h.h:3:6'))
        self.assertFalse(self.has('Shared', 'decl', 'h.h:2:6'))
        self.assertTrue(self.has('Other', 'call', 'b.cpp:2:12'))  # 解析し直さない TU の行は残る

        # TU を足す・消す・コマンドを変える
        self.write('c.cpp', '#include "h.h"\nvoid C() { Shared(); }\n', [])
        del self.cmds['b.cpp']
        self.write('a.cpp', '#include "h.h"\nvoid A() { Shared(); }\n', ['/DCHANGED'])
        os.utime(os.path.join(self.root, 'a.cpp'), (time.time() - 3600,) * 2)  # 中身の時刻は古いまま
        rc, err = self.run_ltidx('-update')
        self.assertIn('2/2 TU to re-index, 1 TU removed', err)
        self.assertTrue(self.has('Shared', 'call', 'c.cpp:2:12'))
        self.assertFalse(any(r[3].startswith('b.cpp:') for r in self.rows()))  # 消えた TU だけが読んでいたファイル
        self.assertEqual(set(self.tus()), {'a.cpp', 'c.cpp'})

        # 索引の中身を変える ltidx のオプションを変えたら、全 TU を解析し直す
        rc, err = self.run_ltidx('-update', '-no-dep')
        self.assertIn('2/2 TU to re-index', err)
        rc, err = self.run_ltidx('-update')
        self.assertIn('2/2 TU to re-index', err)

        # 全体の作り直しと同じになる
        self.assertSameAsFull()

    def assertSameAsFull(self):
        """-update で作った索引(index.tsv)が、全体の作り直し(full.tsv)と行の集合で一致する。"""
        rc, err = self.run_ltidx(out='.index/full.tsv')
        self.assertEqual(rc, 0, err)
        for suffix in ('', '.skip.tsv', '.deps.tsv'):
            with open(os.path.join(self.root, '.index', 'index.tsv' + suffix), encoding='utf-8') as f:
                u = set(f)
            with open(os.path.join(self.root, '.index', 'full.tsv' + suffix), encoding='utf-8') as f:
                full = set(f)
            self.assertEqual(u - full, set(), 'stale rows in index.tsv' + suffix)
            self.assertEqual(full - u, set(), 'missing rows in index.tsv' + suffix)
        # 行の出どころも同じになる(次の -update も正しくなる)
        self.assertEqual(self.own('.index/index.tsv'), self.own('.index/full.tsv'))

    def own(self, out):
        """<out>.own.bin の中身。(正確か, {(TU, ファイル, 行のハッシュ)})。"""
        with open(os.path.join(self.root, out + '.own.bin'), 'rb') as f:
            b = f.read()
        pos = 0

        def take(n):
            nonlocal pos
            pos += n
            return b[pos - n:pos]

        def u32():
            return struct.unpack('<I', take(4))[0]

        self.assertEqual(take(8), b'LTOWN001')
        take(8)  # .tu.tsv のハッシュ
        exact = take(1) == b'\x01'
        files = [take(u32()).decode('utf-8') for _ in range(u32())]
        rows = set()
        for _ in range(u32()):
            tu = os.path.basename(take(u32()).decode('utf-8'))
            for _ in range(u32()):
                f, n = u32(), u32()
                rows |= {(tu, files[f], h) for h in struct.unpack(f'<{n}Q', take(8 * n))}
        self.assertEqual(pos, len(b))
        return exact, rows

    def test_update_shared_header(self):
        # 変わっていない共有のヘッダーの行のうち、解析し直す TU だけが出していた行(実体化・マクロ由来)を消す
        h = ('#pragma once\n'
             'template <class T> struct Box { void Put(T t) { t.Draw(); } };\n'
             '#ifdef FEATURE\n'
             'void Feature();\n'
             '#else\n'
             'void NoFeature();\n'
             '#endif\n')
        self.write('h.h', h)
        self.write('a.cpp', '#define FEATURE\n#include "h.h"\nstruct A { void Draw(); };\n'
                   'void UseA(Box<A> b, A a) { b.Put(a); }\n', [])
        self.write('b.cpp', '#include "h.h"\nstruct B { void Draw(); };\n'
                   'void UseB(Box<B> b, B x) { b.Put(x); }\n', [])
        rc, err = self.run_ltidx()
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.has('A::Draw', 'call', 'h.h:2:51'))
        self.assertTrue(self.has('Feature', 'decl', 'h.h:4:6'))

        # a.cpp だけを変える。h.h の A::Draw の行と Feature の行は、a.cpp だけが出していた
        self.touch_later('a.cpp', '#include "h.h"\nstruct A { void Draw(); };\nvoid UseA(A a) { a.Draw(); }\n')
        rc, err = self.run_ltidx('-update')
        self.assertIn('1/2 TU to re-index', err)
        self.assertFalse(self.has('A::Draw', 'call', 'h.h:2:51'))
        self.assertFalse(self.has('Feature', 'decl', 'h.h:4:6'))
        self.assertTrue(self.has('B::Draw', 'call', 'h.h:2:51'))  # 残す TU(b.cpp)の行は残る
        self.assertSameAsFull()
        self.assertTrue(any(f == 'h.h' for _, f, _ in self.own('.index/index.tsv')[1]))  # 共通でない行がある

        # 1 つの TU だけが読んでいたヘッダーを、新しい TU も読むようになった後に、前からの TU だけを変える
        self.write('g.h', '#pragma once\ntemplate <class T> void Each(T t) { t.Draw(); }\n')
        self.touch_later('a.cpp', '#include "g.h"\nstruct A { void Draw(); };\nvoid UseA(A a) { Each(a); }\n')
        rc, err = self.run_ltidx('-update')
        self.assertEqual(rc, 0, err)
        os.utime(os.path.join(self.root, 'a.cpp'), (time.time() - 3600,) * 2)  # 次の -update では変わっていない
        self.write('c.cpp', '#include "g.h"\nstruct C { void Draw(); };\nvoid UseC(C c) { Each(c); }\n', [])
        rc, err = self.run_ltidx('-update')
        self.assertIn('1/3 TU to re-index', err)
        self.assertSameAsFull()
        self.touch_later('a.cpp', '#include "g.h"\nstruct A { void Draw(); };\nvoid UseA(A a) { a.Draw(); }\n')
        rc, err = self.run_ltidx('-update')
        self.assertIn('1/3 TU to re-index', err)
        self.assertFalse(self.has('A::Draw', 'call', 'g.h:2:39'))
        self.assertTrue(self.has('C::Draw', 'call', 'g.h:2:39'))
        self.assertSameAsFull()

    def test_update_without_provenance(self):
        # 行の出どころ(.own.bin)が無い・別の回のもののときは、前回の行をすべて残し(古い行が残りうる)、そう知らせる
        self.write('h.h', '#pragma once\ntemplate <class T> void Each(T t) { t.Draw(); }\n')
        self.write('a.cpp', '#include "h.h"\nstruct A { void Draw(); };\nvoid UseA(A a) { Each(a); }\n', [])
        self.write('b.cpp', '#include "h.h"\nstruct B { void Draw(); };\nvoid UseB(B b) { Each(b); }\n', [])
        rc, err = self.run_ltidx()
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.has('A::Draw', 'call', 'h.h:2:39'))
        own = os.path.join(self.root, '.index', 'index.tsv.own.bin')
        os.remove(own)
        self.touch_later('a.cpp', '#include "h.h"\nstruct A { void Draw(); };\nvoid UseA(A a) { a.Draw(); }\n')
        rc, err = self.run_ltidx('-update')
        self.assertEqual(rc, 0, err)
        self.assertIn('index.tsv.own.bin)が無い', err)
        self.assertTrue(self.has('A::Draw', 'call', 'h.h:2:39'))  # 古い行が残る(前の版と同じ)
        # 正確でない出どころは、全体を作り直すまで正確でないままにする
        rc, err = self.run_ltidx('-update')
        self.assertIn('index.tsv.own.bin)が不正確', err)
        # 別の回の索引の .own.bin は使わない
        rc, err = self.run_ltidx(out='.index/full.tsv')
        self.assertEqual(rc, 0, err)
        shutil.copyfile(os.path.join(self.root, '.index', 'full.tsv.own.bin'), own)
        rc, err = self.run_ltidx('-update')
        self.assertIn('.tu.tsv と組でない', err)
        # 作り直せば正確になる
        rc, err = self.run_ltidx()
        self.assertEqual(rc, 0, err)
        self.assertFalse(self.has('A::Draw', 'call', 'h.h:2:39'))
        rc, err = self.run_ltidx('-update')
        self.assertNotIn('own.bin', err)

    def test_replace_while_open(self):
        # 索引のどれかが開かれている間は差し替えを待つ。開いたままなら、新旧を混ぜずに前の索引を残す
        self.write('a.cpp', 'void A() {}\n', [])
        rc, err = self.run_ltidx()
        self.assertEqual(rc, 0, err)
        d = os.path.join(self.root, '.index')
        names = ['index.tsv', 'index.tsv.tu.tsv', 'index.tsv.skip.tsv', 'index.tsv.deps.tsv', 'index.tsv.own.bin']

        def snapshot():
            out = {}
            for n in names:
                with open(os.path.join(d, n), 'rb') as f:
                    out[n] = f.read()
            return out

        before = snapshot()
        self.write('a.cpp', 'void A() {}\nvoid B() {}\n')
        with open(os.path.join(d, 'index.tsv.skip.tsv'), 'rb'):  # Python は FILE_SHARE_DELETE 無しで開く
            rc, err = self.run_ltidx('-update')
        self.assertEqual(rc, 2, err)
        self.assertIn('the previous index is kept', err)
        self.assertEqual(snapshot(), before)

        # 途中で閉じれば差し替わり、退避したファイルも残らない
        import threading
        f = open(os.path.join(d, 'index.tsv.skip.tsv'), 'rb')
        threading.Timer(1.0, f.close).start()
        rc, err = self.run_ltidx('-update')
        self.assertEqual(rc, 0, err)
        self.assertTrue(self.has('B', 'def', 'a.cpp:2:6'))
        self.assertEqual(sorted(os.listdir(d)), sorted(names))

    def test_root_written_differently(self):
        # ルートを 8.3 形式の短い名前で渡し、compile_commands.json は長い名前(MSBuild が返す形)。どちらでもルート配下と判定する
        import ctypes
        long_dir = os.path.realpath(os.path.join(self.root, 'a-long-directory-name'))  # 一時フォルダ自体が短い名前のことがある
        buf = ctypes.create_unicode_buffer(1024)
        os.makedirs(long_dir)
        if not ctypes.windll.kernel32.GetShortPathNameW(long_dir, buf, 1024) or buf.value.lower() == long_dir.lower():
            self.skipTest('8.3 names are not available on this volume')
        short_dir = buf.value
        self.root = long_dir
        self.write('a.cpp', 'void Run();\nvoid Main() { Run(); }\n', [])
        for root in (short_dir, long_dir):
            r = subprocess.run([LTIDX, '-p', long_dir, '-root', root, '-o', os.path.join(long_dir, 'o.tsv')],
                               capture_output=True, text=True, encoding='utf-8', errors='replace')
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertTrue(self.has('Run', 'call', 'a.cpp:2:15', out='o.tsv'), root)
        self.root = os.path.dirname(long_dir)  # tearDown で消す(実体のパスで同じフォルダ)

    def test_update_needs_previous_index(self):
        self.write('a.cpp', 'void A() {}\n', [])
        rc, err = self.run_ltidx('-update')
        self.assertEqual(rc, 2)
        self.assertIn('-update', err)

    def touch_later(self, rel, text):
        """索引の時刻(解析を始めた時刻)より確実に新しい時刻で書く。"""
        self.write(rel, text)
        t = time.time() + 2
        os.utime(os.path.join(self.root, rel), (t, t))

    def ltq(self, *args):
        import contextlib
        import io
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = ltq.main(['--root', self.root] + list(args))
        self.assertEqual(code, 0, out.getvalue())
        return out.getvalue()


if __name__ == '__main__':
    unittest.main()
