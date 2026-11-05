"""llvm-notices.py のテスト(ネットワークは使わない。--from で、合成した LLVM のソースの写しを読ませる)。
- 各部品の表示(目印を含むコメント)を写し、行頭の * を外すこと
- 目印が見つからない(LLVM の版で表示の形が変わった)ときは、欠けた表示を作らずに失敗すること

usage: python -I -m unittest discover -s scripts/test   (リポジトリのルートで)
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'llvm-notices.py')
FILES = {
    'COPYRIGHT.regex': 'regex notice line 1\nregex notice line 2\n',
    'xxhash.cpp': '/*\n *  xxHash\n *  by Yann Collet\n *  BSD 2-Clause\n */\nint x;\n',
    'ConvertUTF.cpp': '/* SPDX */\n/*\n * Distributed under the Terms of Use in x.\n * Permission granted.\n */\n',
    'UnicodeNameToCodepointGenerated.cpp': '// gen\n/*\nUNICODE, INC. LICENSE AGREEMENT - DATA FILES AND SOFTWARE\nterms\n*/\n',
    'MD5.cpp': '/*\n * Alexander Peslyak, public domain\n */\n#include "x"\n',
}


class LlvmNoticesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix='cri')
        self.src = os.path.join(self.tmp, 'src')
        os.makedirs(self.src)
        for n, t in FILES.items():
            with open(os.path.join(self.src, n), 'w', encoding='utf-8', newline='\n') as f:
                f.write(t)
        self.out = os.path.join(self.tmp, 'out', 'THIRD-PARTY-NOTICES.txt')

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_it(self):
        return subprocess.run([sys.executable, '-I', SCRIPT, '--version', '9.9.9', '--from', self.src, '--out', self.out],
                              capture_output=True, text=True, encoding='utf-8', errors='replace')

    def test_copies_each_notice(self):
        r = self.run_it()
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(self.out, encoding='utf-8') as f:
            text = f.read()
        self.assertIn('LLVM 9.9.9', text)
        self.assertIn('regex notice line 1\nregex notice line 2', text)
        self.assertIn(' xxHash\n by Yann Collet\n BSD 2-Clause', text)  # 行頭の * を外す(その後の字下げは原文のまま)
        self.assertFalse([l for l in text.splitlines() if l.lstrip().startswith('*')])
        self.assertNotIn('int x;', text)  # コメントの外は写さない
        self.assertIn('Distributed under the Terms of Use in x.\nPermission granted.', text)
        self.assertNotIn('SPDX', text)
        self.assertIn('UNICODE, INC. LICENSE AGREEMENT - DATA FILES AND SOFTWARE\nterms', text)
        self.assertIn('Alexander Peslyak, public domain', text)

    def test_missing_marker_fails(self):
        with open(os.path.join(self.src, 'xxhash.cpp'), 'w', encoding='utf-8') as f:
            f.write('/* moved elsewhere */\n')
        r = self.run_it()
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('xxhash.cpp', r.stderr)
        self.assertFalse(os.path.exists(self.out))


if __name__ == '__main__':
    unittest.main()
