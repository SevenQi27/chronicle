"""Start the local SQLite MCP server or the read-only graph UI."""

import argparse
import asyncio
import errno
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tomllib

from . import __version__
from .errors import error_result
from .sqlite_mcp import serve
from .ui import serve_ui


def add_common_arguments(subparser):
    subparser.add_argument('--sqlite', type=Path, metavar='PATH',
                           help='数据库路径')
    subparser.add_argument('--namespace', help='记忆分区')


def mcp_transport():
    try:
        result = subprocess.run(['codex', 'mcp', 'get', 'chronicle', '--json'],
                                capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is not None and result.returncode == 0:
        entry = json.loads(result.stdout)
        return entry['transport'] if entry.get('enabled', True) else {}

    config_path = Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').expanduser() / 'config.toml'
    if config_path.is_file():
        entry = tomllib.loads(config_path.read_text(encoding='utf-8')).get('mcp_servers', {}).get('chronicle')
        if entry is not None:
            return entry if entry.get('enabled', True) else {}

    project = Path('.mcp.json')
    if project.is_file():
        return json.loads(project.read_text(encoding='utf-8')).get('mcpServers', {}).get('chronicle', {})
    return {}


def ui_defaults(path, namespace):
    if path is not None and namespace is not None:
        return path, namespace
    transport = mcp_transport()
    parser = argparse.ArgumentParser(prog='Chronicle MCP 启动参数', add_help=False, allow_abbrev=False)
    add_common_arguments(parser)
    configured, _ = parser.parse_known_args(transport.get('args') or [])
    if path is None and configured.sqlite is not None:
        cwd = Path(transport.get('cwd') or Path.cwd()).expanduser()
        path = cwd / configured.sqlite.expanduser()
    if namespace is None:
        namespace = configured.namespace
    return path, namespace


def main():
    parser = argparse.ArgumentParser(description='Chronicle 本地记忆')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)

    mcp = commands.add_parser('mcp', help='启动 SQLite stdio MCP 服务')
    add_common_arguments(mcp)

    ui = commands.add_parser('ui', help='启动本地只读 3D 图谱',
                             description='启动本地只读 3D 图谱，未指定的库路径和分区沿用已配置的 Chronicle MCP。')
    add_common_arguments(ui)
    ui.add_argument('--port', type=int, default=8765, help='监听端口，默认 8765')

    args = parser.parse_args()
    try:
        path, namespace = args.sqlite, args.namespace
        if args.command == 'ui':
            path, namespace = ui_defaults(path, namespace)
        if path is None:
            path = Path.home() / '.local' / 'share' / 'chronicle' / 'memory.sqlite3'
        path = path.expanduser().resolve()
        if namespace is None:
            namespace = 'default'
        if args.command == 'mcp':
            asyncio.run(serve(path, namespace))
        elif args.command == 'ui':
            serve_ui(path, namespace, args.port)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (ValueError, OSError, sqlite3.Error) as error:
        if args.command == 'ui' and isinstance(error, OSError) and error.errno == errno.EADDRINUSE:
            failure = {'ok': False, 'error': {
                'code': 'port_in_use',
                'message': f'端口 {args.port} 已被占用。可以用 --port 指定其他端口。',
            }}
        else:
            failure = error_result(error)
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2 if failure['error']['code'] == 'invalid_arguments' else 4) from None
