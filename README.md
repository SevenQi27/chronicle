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

`chronicle ui` uses the database and namespace from the configured Chronicle MCP connection. Explicit `--sqlite` and `--namespace` options take precedence. Open <http://127.0.0.1:8765/> for the 3D graph.

## MCP tools

| Tool | Use |
| --- | --- |
| `memory_write` | Save sourced experiences and understandings. An understanding's `supports` must refer to experiences in the same namespace by logical ID or version ID. |
| `memory_search`, `memory_context` | Recall matching memories by indexed terms. Optional filters cover origin, workspace, conversation, and source. |
| `memory_details` | Read a version and its evidence by `record_id`. Follow an understanding's `support_record_ids` to read the supporting experiences and their original sources. |
| `memory_visibility` | Use `withdraw` or `restore` with `memory_ids` containing logical memory IDs or version IDs. |

To correct a memory, use the same logical ID, provide a new source ID, and set `replaces` to the current version's `record_id`. Recall supports `known_at` for the knowledge cutoff and `at` for the effective time. Chronicle resolves logical IDs in `supports` to specific versions at write time. Use `source_mode=summary` to omit source text.

Keywords, aliases and explicit `alternatives` provide recall without a model service.

Withdrawal hides every version of the selected memory from default search, details, and visual views. Historical queries also apply the current withdrawal state. Sources and versions remain available by version ID with `memory_details.include_withdrawn=true`. Restoring a memory makes it searchable again.

Run the local acceptance checks with `uv run python -m pytest -q`. See [the testing guide](docs/testing.md) for the end-to-end example.
