# Chronicle Memory

Chronicle is a local MCP server for remembering experiences and their original sources. It stores data in SQLite and keeps previous versions when an experience is corrected.

[简体中文](README.zh-CN.md)

## Quick start

Requirements are Python 3.11 or newer with SQLite FTS5, and uv. From this checkout:

```sh
uv sync --locked
uv run chronicle mcp
```

Configure your MCP host to use the installed `chronicle` command with `mcp` as its argument. Chronicle starts over stdio and initializes its schema and search index on the first launch. The default database is `~/.local/share/chronicle/memory.sqlite3`.

For a custom path or namespace:

```sh
chronicle mcp --sqlite /absolute/path/memory.sqlite3 --namespace personal
```

Use the same path and namespace after restarting to read the same memories. Existing Chronicle SQLite databases are indexed automatically on startup.

## MCP tools

| Tool | Use |
| --- | --- |
| `memory_write` | Save sourced experiences and understandings. An understanding's `supports` must refer to experiences in the same namespace by logical ID or version ID. |
| `memory_search`, `memory_context` | Recall matching memories by indexed terms. Optional filters cover origin, workspace, conversation, and source. |
| `memory_details` | Read a version and its evidence by `record_id`. Follow an understanding's `support_record_ids` to read the supporting experiences and their original sources. |

To correct a memory, use the same logical ID, provide a new source ID, and set `replaces` to the current version's `record_id`. Recall supports `known_at` for the knowledge cutoff and `at` for the effective time. Chronicle resolves logical IDs in `supports` to specific versions at write time. Use `source_mode=summary` to omit source text.

Keywords, aliases and explicit `alternatives` provide recall without a model service.

Run the local acceptance checks with `uv run python -m pytest -q`. See [the testing guide](docs/testing.md) for the end-to-end example.
