"""code-ref-index の MCP サーバー(stdio)。ltq の refs / check をツールとして公開する。

  python -I server.py [--root <ソースのルート>] [--index <索引.tsv>]

標準ライブラリだけで、MCP の stdio トランスポート(1 行 1 メッセージの JSON-RPC 2.0)を話す。
標準出力はプロトコル専用なので、ログは標準エラーに出す。
"""
import argparse
import contextlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'query'))
import ltq  # noqa: E402

VERSION = '0.2.0'
PROTOCOL = '2025-06-18'

INSTRUCTIONS = (
    'C++ reference index built by clang for this workspace. Before grepping C++ sources for callers, '
    'references or definitions, call `refs`. Its `calls` section can be used as the answer directly; '
    'matches it reports as inside comments are not code. Use `check` for lines found by other searches.')

TOOLS = [
    {
        'name': 'refs',
        'title': 'C++ references',
        'description': (
            'Find every call site, reference, definition and declaration of a C++ symbol, resolved by clang '
            'over all translation units (including template instantiations). Use this instead of grep for C++ '
            'symbol searches. One call returns: calls (with the enclosing function), other references, '
            'definitions, virtual overrides (calls through a base class that may dispatch to the symbol), '
            'unresolved calls inside templates with the same name (decide those from the source '
            'line), and every text match of the name that is NOT in the index with the reason '
            '(comment, inactive #if, macro definition, string literal, file not analyzed). '
            'The calls section needs no re-verification with grep.'),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'name': {'type': 'string',
                         'description': 'Qualified name or a suffix of it, e.g. "nox::io::File::Read" or "File::Read"'},
                'limit': {'type': 'integer', 'description': 'Max lines per section (default 200)'},
                'exclude': {'type': 'array', 'items': {'type': 'string'},
                            'description': 'Directory names or root-relative path prefixes to skip in the text search'},
            },
            'required': ['name'],
        },
        'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    },
    {
        'name': 'check',
        'title': 'C++ index check',
        'description': (
            'For each path:line[:col], report the C++ references the clang index has on that line, and whether '
            'the line is inside a comment or an inactive #if branch. Use it for lines found by grep or other '
            'searches before reporting them.'),
        'inputSchema': {
            'type': 'object',
            'properties': {
                'locations': {'type': 'array', 'items': {'type': 'string'},
                              'description': 'Root-relative locations such as "src/a.cpp:42" or "src/a.cpp:42:7"'},
            },
            'required': ['locations'],
        },
        'annotations': {'readOnlyHint': True, 'openWorldHint': False},
    },
]


def run_ltq(base, args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            code = ltq.main(base + args)
        except SystemExit as e:  # argparse の誤り
            code = e.code or 2
    return code, out.getvalue()


def call_tool(base, name, a):
    if name == 'refs':
        args = ['refs', str(a['name'])]
        if a.get('limit'):
            args += ['--limit', str(int(a['limit']))]
        for x in a.get('exclude') or []:
            args += ['--exclude', str(x)]
    elif name == 'check':
        locs = a.get('locations') or []
        if isinstance(locs, str):
            locs = locs.split()
        if not locs:
            return 2, 'no locations'
        args = ['check'] + [str(x) for x in locs]
    else:
        raise KeyError(name)
    return run_ltq(base, args)


def handle(msg, base):
    method, mid = msg.get('method'), msg.get('id')
    if method == 'initialize':
        ver = (msg.get('params') or {}).get('protocolVersion') or PROTOCOL
        return {'protocolVersion': ver, 'capabilities': {'tools': {}},
                'serverInfo': {'name': 'code-ref-index', 'version': VERSION}, 'instructions': INSTRUCTIONS}
    if method == 'ping':
        return {}
    if method == 'tools/list':
        return {'tools': TOOLS}
    if method == 'tools/call':
        p = msg.get('params') or {}
        try:
            code, text = call_tool(base, p.get('name'), p.get('arguments') or {})
        except KeyError:
            raise RpcError(-32602, f'unknown tool: {p.get("name")}')
        except Exception as e:  # 索引の読み込み失敗など。ツールの誤りとして返す
            code, text = 1, f'{type(e).__name__}: {e}'
        return {'content': [{'type': 'text', 'text': text}], 'isError': code != 0}
    if mid is None:
        return None  # 通知(notifications/initialized など)には返さない
    raise RpcError(-32601, f'method not found: {method}')


class RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def main():
    ap = argparse.ArgumentParser(prog='code-ref-index-mcp')
    ap.add_argument('--root', default=os.environ.get('CRI_ROOT', '.'), help='source root (default: cwd)')
    ap.add_argument('--index', default=os.environ.get('CRI_INDEX'), help='index TSV (default <root>/.index/...)')
    a = ap.parse_args()
    base = ['--root', os.path.abspath(a.root)] + (['--index', os.path.abspath(a.index)] if a.index else [])
    print(f'code-ref-index mcp {VERSION}: root={os.path.abspath(a.root)}', file=sys.stderr)

    stdin = io.TextIOWrapper(sys.stdin.buffer, encoding='utf-8')
    stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', newline='\n')
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            reply = {'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'parse error'}}
        else:
            if 'method' not in msg:
                continue  # 応答(このサーバーは要求を出さない)
            try:
                result = handle(msg, base)
                if result is None:
                    continue
                reply = {'jsonrpc': '2.0', 'id': msg.get('id'), 'result': result}
            except RpcError as e:
                reply = {'jsonrpc': '2.0', 'id': msg.get('id'), 'error': {'code': e.code, 'message': str(e)}}
        stdout.write(json.dumps(reply, ensure_ascii=False) + '\n')
        stdout.flush()


if __name__ == '__main__':
    main()
