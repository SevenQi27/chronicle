# Chronicle Memory

Chronicle is a local MCP server for remembering experiences and their original sources. It stores data in SQLite and keeps previous versions when an experience is corrected.

[简体中文](README.zh-CN.md)

## Quick start

Build with Go 1.24 or later. SQLite and its FTS5 index are included in the executable. From this checkout:

```sh
go install ./cmd/chronicle
```

Configure your MCP host to use the absolute path to the installed `chronicle` executable with `mcp` as its argument. Chronicle starts over stdio and initializes its schema and search index on the first launch. The default database is `~/.local/share/chronicle/memory.sqlite3`.

For a custom path or namespace:

```sh
chronicle mcp --sqlite /absolute/path/memory.sqlite3 --namespace personal
```

Use the same path and namespace after restarting to read the same memories. When switching an existing installation to this executable, preserve its `--sqlite`, `--namespace`, and any `--capture-queue` arguments. Existing Chronicle SQLite databases are indexed automatically on startup.

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

## Automatic conversation capture

The MCP server drains the queue for its database and namespace while connected. Any host can submit events with `chronicle capture submit --input event.json`, using the same `--sqlite` and `--namespace` options as the MCP server. Events contain `origin`, `turn_id`, `source`, and optional `workspace` and `private` fields. The source contains the message ID, conversation ID, role, text, and timestamp with a time zone.

### Codex adapter

Install the optional Codex adapter from the repository.

```sh
chronicle capture setup-codex
```

The installer uses the configured Chronicle MCP storage in Codex and preserves existing hooks. Review the `UserPromptSubmit` and `Stop` hooks in Codex. New messages queue locally while MCP is disconnected and are archived when it reconnects.

`chronicle capture status` reports queue and retry status. `saved` means the worker committed a version to the memory database. Use `memory_search` to find a turn and `memory_details` to read its original messages and their roles. The queue excludes private turns and redacts marked spans and recognized credential formats before storing messages. Capture runs without a model service.

For continuous capture without an MCP connection, run `chronicle capture worker`, or add `--once` to process one batch. For an existing custom queue, use `--capture-queue PATH` on the MCP server and `--queue PATH` on the capture commands.

## Tests

Run the local acceptance checks with `go test ./...`. See [the testing guide](docs/testing.md) for details.
