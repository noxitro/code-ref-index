"""setup.ps1 のテスト。LLVM の場所が間違っているときに、ビルドに進まず、indexer/llvm-root.txt を書き換えないことを確かめる。
pwsh が要る(無ければ飛ばす)。ビルドと索引づくりは index-project・ltidx のテストが確かめる。

usage: python -I -m unittest discover -s scripts/test   (リポジトリのルートで)
"""
import os
import shutil
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, '..', 'setup.ps1')
ROOT_FILE = os.path.join(HERE, '..', '..', 'indexer', 'llvm-root.txt')
PWSH = shutil.which('pwsh')


def read_or_none(path):
    try:
        with open(path, 'rb') as f:
            return f.read()
    except FileNotFoundError:
        return None


@unittest.skipUnless(PWSH, 'pwsh is required')
class SetupTest(unittest.TestCase):
    def test_wrong_llvm_folder_is_rejected(self):
        before = read_or_none(ROOT_FILE)
        with tempfile.TemporaryDirectory(prefix='cri') as d:  # LLVM ではない空のフォルダ
            env = {k: v for k, v in os.environ.items() if k.upper() != 'LLVM_ROOT'}
            r = subprocess.run([PWSH, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', SCRIPT,
                                '-LlvmRoot', d, '-NoIndex'],
                               capture_output=True, text=True, encoding='oem', errors='replace', env=env)
        self.assertNotEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('ClangConfig.cmake', r.stdout + r.stderr)
        self.assertNotIn('ビルドします', r.stdout)
        self.assertEqual(read_or_none(ROOT_FILE), before)


if __name__ == '__main__':
    unittest.main()
