# SQLite 测试

```sh
uv sync --locked
uv run python -m pytest -q
```

测试使用临时文件。MCP 端到端测试通过真实 stdio 握手、写入、关键词改写与别名召回、更正、历史查询和重启后读回。另有已有 SQLite 文件补建索引与默认启动路径的覆盖。

也可单独运行端到端脚本：

```sh
uv run python scripts/smoke_sqlite_mcp.py
```
