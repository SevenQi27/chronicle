"""Structured errors for the SQLite MCP and CLI."""

import sqlite3


class ConflictError(ValueError):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code = code
        self.details = details


def error_result(error):
    if isinstance(error, ConflictError):
        detail = {'code': error.code, 'message': str(error), **error.details}
    elif isinstance(error, sqlite3.OperationalError) and str(error) == 'no such module: fts5':
        detail = {'code': 'storage_error', 'message': '当前 Python 的 SQLite 未启用 FTS5'}
    elif isinstance(error, (sqlite3.Error, OSError)):
        detail = {'code': 'storage_error', 'message': '无法读取或写入 SQLite 数据库'}
    else:
        detail = {'code': 'invalid_arguments', 'message': str(error)}
    return {'ok': False, 'error': detail}
