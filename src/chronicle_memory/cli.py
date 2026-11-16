"""Start the local SQLite MCP server."""

import argparse
import asyncio
import json
from pathlib import Path
import sqlite3
import sys

from . import __version__
from .errors import error_result
from .sqlite_mcp import serve


def main():
    parser = argparse.ArgumentParser(description='Chronicle 本地记忆')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', required=True)
    mcp = commands.add_parser('mcp', help='启动 SQLite stdio MCP 服务')
    mcp.add_argument('--sqlite', type=Path, metavar='PATH',
                     help='数据库路径，默认 ~/.local/share/chronicle/memory.sqlite3')
    mcp.add_argument('--namespace', default='default', help='记忆分区，默认 default')
    args = parser.parse_args()
    path = args.sqlite or Path.home() / '.local' / 'share' / 'chronicle' / 'memory.sqlite3'
    try:
        asyncio.run(serve(path.expanduser().resolve(), args.namespace))
    except KeyboardInterrupt:
        raise SystemExit(130) from None
    except (ValueError, OSError, sqlite3.Error) as error:
        failure = error_result(error)
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2 if failure['error']['code'] == 'invalid_arguments' else 4) from None
