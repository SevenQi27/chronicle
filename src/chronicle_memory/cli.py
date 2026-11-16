"""Start the local SQLite MCP server or the read-only Ledger UI."""

import argparse
import asyncio
import json
from pathlib import Path
import sqlite3
import sys

from . import __version__
from .errors import error_result
from .sqlite_mcp import serve
from .ui import serve_ui


def add_common_arguments(subparser):
    subparser.add_argument('--sqlite', type=Path, metavar='PATH',
                           help='数据库路径，默认 ~/.local/share/chronicle/memory.sqlite3')
    subparser.add_argument('--namespace', default='default', help='记忆分区，默认 default')


def main():
    parser = argparse.ArgumentParser(description='Chronicle 本地记忆')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)

    mcp = commands.add_parser('mcp', help='启动 SQLite stdio MCP 服务')
    add_common_arguments(mcp)

    ui = commands.add_parser('ui', help='启动本地只读认知账本与时间机器 Web 界面')
    add_common_arguments(ui)
    ui.add_argument('--port', type=int, default=8765, help='监听端口，默认 8765')

    args = parser.parse_args()
    path = (args.sqlite or Path.home() / '.local' / 'share' / 'chronicle' / 'memory.sqlite3').expanduser().resolve()
    try:
        if args.command == 'mcp':
            asyncio.run(serve(path, args.namespace))
        elif args.command == 'ui':
            serve_ui(path, args.namespace, args.port)
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (ValueError, OSError, sqlite3.Error) as error:
        failure = error_result(error)
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2 if failure['error']['code'] == 'invalid_arguments' else 4) from None
