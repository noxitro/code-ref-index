"""setup-instructions.ps1 のテスト。一時フォルダの .github/copilot-instructions.md に足して消し、元に戻ることを確かめる。
pwsh が要る(無ければ飛ばす)。

usage: python -I -m unittest discover -s scripts/test   (リポジトリのルートで)
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, '..', 'setup-instructions.ps1')
PWSH = shutil.which('pwsh')


@unittest.skipUnless(PWSH, 'pwsh is required')
class SetupInstructionsTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix='cri')
        self.file = os.path.join(self.root, '.github', 'copilot-instructions.md')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def run_ps(self, action):
        r = subprocess.run([PWSH, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', SCRIPT,
                            '-Root', self.root, '-Action', action],
                           capture_output=True, text=True, errors='replace')  # 出力の文字コードは起動のしかたで変わるので、中身はファイルで確かめる
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def write(self, data):
        os.makedirs(os.path.dirname(self.file), exist_ok=True)
        with open(self.file, 'wb') as f:
            f.write(data)

    def read(self):
        with open(self.file, 'rb') as f:
            return f.read()

    def test_new_file_is_created_and_removed(self):
        self.run_ps('Add')
        text = self.read().decode('utf-8')
        self.assertTrue(text.startswith('<!-- cxxref:begin'))
        self.assertIn('`refs`', text)
        self.assertNotIn('\r\n', text)
        self.run_ps('Remove')
        self.assertFalse(os.path.exists(self.file))  # 足したときに作ったファイルなので消す

    def test_round_trip_keeps_other_text(self):
        orig = '﻿# 既存の指示\r\n\r\n- 日本語で答える\r\n'.encode('utf-8')
        self.write(orig)
        self.run_ps('Add')
        text = self.read()
        self.assertTrue(text.startswith(orig))
        self.assertNotIn(b'\n', text.replace(b'\r\n', b''))  # 元の CRLF に合わせる
        self.run_ps('Remove')
        self.assertEqual(self.read(), orig)

    def test_add_twice_replaces(self):
        self.write(b'# A\n')
        self.run_ps('Add')
        once = self.read()
        self.write(once.replace(b'`refs`', b'`old`'))  # 古い中身が入っている状態
        self.run_ps('Add')
        self.assertEqual(self.read(), once)

    def test_block_in_the_middle(self):
        self.write(b'# A\n')
        self.run_ps('Add')
        with open(self.file, 'ab') as f:
            f.write(b'\n# B\n')  # 足した後にユーザーが書き足した
        self.run_ps('Remove')
        self.assertEqual(self.read(), b'# A\n\n# B\n')

    def test_remove_when_absent(self):
        self.write(b'# A\n')
        self.run_ps('Remove')
        self.assertEqual(self.read(), b'# A\n')


if __name__ == '__main__':
    unittest.main()
