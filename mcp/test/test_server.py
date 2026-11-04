"""MCP サーバーの動作テスト: server.py を子プロセスで起動し、stdio の JSON-RPC で一通り呼ぶ。

usage: python -I -m unittest discover -s mcp/test   (リポジトリのルートで)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

SERVER = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'server.py')


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, 'src'))
        os.makedirs(os.path.join(self.root, '.index'))
        with open(os.path.join(self.root, 'src', 'a.cpp'), 'w', encoding='utf-8', newline='\n') as f:
            f.write('void Run();\nvoid Main() {\n  Run();\n  // Run\n}\n')
        base = os.path.join(self.root, '.index', 'index.tsv')
        with open(base, 'w', encoding='utf-8', newline='\n') as f:
            f.write('Run\tdecl\t\tsrc/a.cpp:1:6\tvoid Run();\n'
                    'Run\tcall\tMain\tsrc/a.cpp:3:3\tRun();\n')
        with open(base + '.skip.tsv', 'w', encoding='utf-8', newline='\n') as f:
            f.write('path\tkind\tbegin\tend\nsrc/a.cpp\tcomment\t4:3\t4:9\n')
        with open(base + '.tu.tsv', 'w', encoding='utf-8', newline='\n') as f:
            f.write(f'file\tok\terrors\tsec\trefs\tfirst_error\n{os.path.join(self.root, "src", "a.cpp")}\t1\t0\t0.1\t2\t\n')
        self.proc = subprocess.Popen([sys.executable, '-I', SERVER, '--root', self.root], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        self.next_id = 0

    def tearDown(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=10)
        self.proc.stdout.close()
        shutil.rmtree(self.root)

    def send(self, method, params=None, notify=False):
        msg = {'jsonrpc': '2.0', 'method': method, 'params': params or {}}
        if not notify:
            self.next_id += 1
            msg['id'] = self.next_id
        self.proc.stdin.write((json.dumps(msg) + '\n').encode('utf-8'))
        self.proc.stdin.flush()
        if notify:
            return None
        reply = json.loads(self.proc.stdout.readline().decode('utf-8'))
        self.assertEqual(reply['id'], self.next_id)
        return reply

    def test_session(self):
        r = self.send('initialize', {'protocolVersion': '2025-06-18', 'capabilities': {},
                                     'clientInfo': {'name': 'test', 'version': '0'}})
        self.assertEqual(r['result']['serverInfo']['name'], 'code-ref-index')
        self.assertIn('tools', r['result']['capabilities'])
        self.send('notifications/initialized', notify=True)  # 通知には返事をしない(次の返事の id で確かめる)

        names = [t['name'] for t in self.send('tools/list')['result']['tools']]
        self.assertEqual(names, ['refs', 'check'])

        r = self.send('tools/call', {'name': 'refs', 'arguments': {'name': 'Run'}})['result']
        self.assertFalse(r['isError'])
        text = r['content'][0]['text']
        self.assertIn('== calls (resolved by clang): 1\n  call   src/a.cpp:3  [in Main]', text)
        self.assertIn('inside comments -> not code; do not report these: 1', text)

        r = self.send('tools/call', {'name': 'check', 'arguments': {'locations': ['src/a.cpp:4:6']}})['result']
        self.assertIn('inside comment 4:3-4:9', r['content'][0]['text'])

        r = self.send('tools/call', {'name': 'nope', 'arguments': {}})
        self.assertEqual(r['error']['code'], -32602)
        r = self.send('no/such/method')
        self.assertEqual(r['error']['code'], -32601)

    def test_missing_index_is_a_tool_error(self):
        shutil.rmtree(os.path.join(self.root, '.index'))
        r = self.send('tools/call', {'name': 'refs', 'arguments': {'name': 'Run'}})['result']
        self.assertTrue(r['isError'])
        self.assertIn('index not found', r['content'][0]['text'])


if __name__ == '__main__':
    unittest.main()
