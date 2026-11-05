"""このリポジトリを Copilot CLI のプラグインとして入れるための定義の検査(Copilot は要らない)。
plugin.json・mcp.json(Agent Plugins 1.0)、com.github.copilot/rules の rule(.mdc)、.github/plugin/marketplace.json。

usage: python -I -m unittest discover -s mcp/test   (リポジトリのルートで)
"""
import json
import os
import re
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..'))
SCHEMA = 'https://agent-plugins.org/schemas/1.0.0/'


def load(rel):
    with open(os.path.join(ROOT, rel), encoding='utf-8') as f:
        return json.load(f)


class PluginTest(unittest.TestCase):
    def test_manifest(self):
        p = load('plugin.json')
        self.assertEqual(p['$schema'], SCHEMA + 'plugin.schema.json')
        # Agent Plugins 1.0 の名前の決まり(小文字・数字・- と .、英数字で始まり終わる)
        self.assertRegex(p['name'], r'^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$')
        allowed = {'$schema', 'name', 'version', 'description', 'author', 'homepage', 'repository', 'license',
                   'keywords', 'extensions'}
        self.assertLessEqual(set(p), allowed)  # 閉じた形式。ほかの欄は無視される
        self.assertRegex(p['version'], r'^\d+\.\d+\.\d+$')

    def test_versions_agree(self):
        # タグ v<版> でリリースを作るとき、CI が同じことを確かめる。get-ltidx は plugin.json の版を取ってくる
        v = load('plugin.json')['version']
        m = load(os.path.join('.github', 'plugin', 'marketplace.json'))
        entry = [x for x in m['plugins'] if x['name'] == load('plugin.json')['name']]
        self.assertEqual(len(entry), 1)
        self.assertEqual(entry[0]['version'], v)
        with open(os.path.join(ROOT, 'mcp', 'server.py'), encoding='utf-8') as f:
            self.assertIn(f"VERSION = '{v}'", f.read())

    def test_mcp_server(self):
        m = load('mcp.json')
        self.assertEqual(m['$schema'], SCHEMA + 'mcp.schema.json')
        s = m['mcpServers']['code-ref-index']  # ツールの名前(code-ref-index-refs など)はこの名前から付く
        self.assertEqual(s['type'], 'stdio')
        script = [a for a in s['args'] if a.startswith('${PLUGIN_ROOT}/')]
        self.assertEqual(len(script), 1)
        self.assertTrue(os.path.exists(os.path.join(ROOT, script[0][len('${PLUGIN_ROOT}/'):])))

    def test_rule(self):
        # Copilot CLI の rule は .mdc(description が必須。alwaysApply・globs)【実測: 1.0.105。.md は読まれなかった】
        d = os.path.join(ROOT, 'com.github.copilot', 'rules')
        files = os.listdir(d)
        self.assertEqual(files, ['cxxref.mdc'])
        with open(os.path.join(d, files[0]), encoding='utf-8') as f:
            text = f.read()
        head = re.match(r'---\n(.*?)\n---\n', text, re.S)
        self.assertTrue(head, 'frontmatter')
        self.assertRegex(head.group(1), r'(?m)^description: \S')
        self.assertRegex(head.group(1), r'(?m)^alwaysApply: true$')
        self.assertIn('`root`', text)  # プラグインのサーバーは作業フォルダを知らないので、root を渡させる


if __name__ == '__main__':
    unittest.main()
