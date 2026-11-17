# Chronicle Memory

**Time-based memory for agents that preserves experiences, evidence, and change over time.**

English · [简体中文](README.zh-CN.md)

**What happened? What did we know then? What was corrected later?** When you add to or correct an experience or understanding, Chronicle saves a new version and retains the earlier version with its sources. An agent can look back to an earlier point in time or follow an understanding to its supporting experiences and original messages.

## One event, two answers from different points in time

Suppose a message recorded on November 3 says the November 20 review is on the third floor. On November 12, a correction confirms the review had been booked on the fifth floor all along. Both messages refer to the same review.

| Question | Time in question | Knowledge cutoff | Retrieved version |
| --- | --- | --- | --- |
| On November 8, where did we think the review would be? | `at` is November 20 | `known_at` is November 8 | Third floor, with the original message |
| Using the later correction, where was it booked? | `at` is November 20 | `known_at` is after November 12 | Fifth floor, with the correction |

The event date is the same in both queries. The available information differs. Earlier versions remain traceable, and searches and detail reads can share the same `known_at` cutoff.

## Three kinds of time

| Time | Stored as | Used for |
| --- | --- | --- |
| **Occurrence time** | An experience's `when.start`, `when.end`, precision, and description | Record when something happened. Approximate dates and descriptions can preserve uncertainty. |
| **Knowledge time** | Chronicle assigns `recorded_at` on write | `known_at` limits recall to versions recorded by a given time. |
| **Effective time** | An understanding can set `valid_from` and `valid_to` | `at` sets the time being asked about. Recall uses it to check whether an understanding has taken effect or expired. |

Original messages and tool results keep their own `source.occurred_at` timestamps. When a message was sent, when the event it describes occurred, and when Chronicle recorded it can differ.

## Evidence, revisions, and visibility

- **Experiences and sources.** Store experiences with their narratives, entities, and original sources. An understanding uses `supports` to point to specific experience versions in the same namespace. Its `basis` distinguishes an explicit user statement from an inference.
- **Updates and corrections.** Keep the logical memory ID, supply a new source ID, and point `replaces` at the current `record_id`. Keyword, entity alias, and caller-provided alternative queries work within the selected time view.
- **Visibility.** Withdrawing a memory hides all its versions from default recall, including historical queries. Chronicle retains the sources and versions. Restoring the memory makes it available to queries again.

## Quick start

Build with Go 1.24 or later. SQLite and its FTS5 index are included in the executable.

```sh
go install ./cmd/chronicle
```

In your agent's MCP configuration, set the command to the absolute path of the installed `chronicle` executable and pass `mcp` as the argument. Chronicle initializes its database and indexes on first use. The default database is `~/.local/share/chronicle/memory.sqlite3`. The following arguments select a specific database and namespace.

```sh
chronicle mcp --sqlite /absolute/path/memory.sqlite3 --namespace personal
```

To connect to an existing store, retain its `--sqlite`, `--namespace`, and any `--capture-queue` arguments.

### MCP tools

| Tool | Use |
| --- | --- |
| `memory_write` | Store a sourced experience or understanding, add information, or correct an earlier version. |
| `memory_search`, `memory_context` | Recall matching memories. Set `at` and `known_at` or filter by origin, workspace, conversation, or source. |
| `memory_details` | Read a version and its evidence by ID. Follow `support_record_ids` to the supporting experience versions. Use `source_mode=summary` to omit source text. |
| `memory_visibility` | Withdraw or restore by memory ID or version ID. Use `memory_details.include_withdrawn=true` to inspect withdrawn versions explicitly. |

## Explore time and evidence in the 3D graph

```sh
chronicle ui
```

Open the [local graph](http://127.0.0.1:8765/). The viewer uses the local Chronicle MCP configuration when available. You can also specify `--sqlite` and `--namespace` explicitly. The timeline slider filters loaded revisions by when Chronicle learned them. Follow the support and evidence links to trace an understanding back to the conversation.

## Optional conversation capture and refinement

A host can submit conversation messages to a local queue. MCP archives the queue while connected and resumes processing after reconnecting. The optional Codex adapter uses `UserPromptSubmit` and `Stop` to capture prompts and final replies. Run the command below, then review the hooks in Codex.

```sh
chronicle capture setup-codex
```

Other hosts can use `chronicle capture submit --input event.json`. The event includes `origin`, `turn_id`, and `source`, with optional `workspace` and `private` fields. Use `chronicle capture status` to check progress. To archive while MCP is disconnected, run `chronicle capture worker` or pass `--once` to process one batch. If you specify a custom queue, use the same file for `--capture-queue` on the MCP server and `--queue` on the capture commands.

Sourced writes, retrieval, and capture run without a model service. To refine complete conversations as they are archived, configure `chronicle mcp` with `--refine-base-url` and `--refine-model`. Use `CHRONICLE_REFINE_API_KEY` if the endpoint requires authentication. MCP sends the turn's messages and relevant understandings to the configured model. Refined understandings link to their evidence, and retries use the same job for an archived version.

## Tests

Run `go test ./...`. See the [testing guide](docs/testing.md) for details.
