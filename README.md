# Chronicle Memory

**Memory for agents that preserves experiences, evidence, and change over time.**

English · [简体中文](README.zh-CN.md)

Chronicle Memory is a Neo4j-backed memory service for AI agents. It connects experiences, people, topics, and evidence-backed understandings in a versioned graph, accessible through a JSON CLI or MCP.

An agent can recall what happened, explain which experiences support an understanding, and distinguish what is known now from what was known at an earlier point in time. Your host agent interprets the conversation; Chronicle stores the result and retrieves it with its sources and temporal context.

This English README is the primary documentation. The Chinese version is maintained alongside it.

## What it does

- **Preserves context.** Store narratives, original messages or tool results, involved entities, and exact or uncertain dates.
- **Connects memories.** Recall through shared people and topics, explicit associations, and evidence links, with reasons for each connection.
- **Grounds understandings in evidence.** Separate explicit user statements from agent inferences and link both to supporting experience versions.
- **Keeps revisions.** Add new information or correct an earlier account without losing the history of what was known.
- **Flags changed evidence.** Mark dependent understandings as `needs_review` when a referenced experience version changes, including both additions and corrections. This requests re-evaluation; it does not declare the understanding false.
- **Tracks temporal state.** Query facts, rules, and plans at a chosen time; a plan becoming due does not mean it was completed.

For example, an agent can connect a rainy camping trip with Lin to a later book-club invitation through the same person. An understanding such as “spending time with Lin matters more than the activity” must cite the experience that supports it. A later correction preserves the old account and makes affected understandings available for review.

## Quick start

Requirements: **Python 3.11+**, **uv**, and **Neo4j**. The bundled installer supports macOS/Linux and installs Neo4j Community **5.26.33**; it requires **Java 21**.

```sh
git clone https://github.com/SevenQi27/chronicle.git
cd chronicle
uv sync --locked

# Install and start a project-local Neo4j instance.
uv run python scripts/setup_neo4j.py
uv run chronicle init
uv run chronicle check
uv run chronicle demo
```

The installer verifies the official archive's SHA-256, installs under `data/runtime/`, and writes connection credentials to `.env` with mode `600`. Temporary downloads are cleaned up. Neo4j listens locally at `bolt://127.0.0.1:7687`; its Browser is at <http://127.0.0.1:7474>.

**Already running Neo4j?** Skip the installer, configure the connection variables in [`.env.example`](.env.example) in your own `.env`, then run `init` and `check`. Initialization creates the required constraints and indexes; ordinary CLI/MCP startup checks that initialization has been completed.

`demo` uses a real database, simulated dates, and an isolated temporary namespace that it cleans up afterward. Core structured writes and keyword/entity/graph retrieval require no separate model API.

### Write and recall your first memory

Run from the repository directory after initialization:

```sh
uv run chronicle remember --namespace tutorial --input - <<'JSON'
{
  "source": {
    "id": "tutorial:message-1",
    "content": "I went camping with Lin last month. Rain made us head home early, but we worked well together.",
    "role": "user",
    "occurred_at": "2026-11-11T09:00:00+08:00"
  },
  "experiences": [{
    "id": "camping-with-lin",
    "title": "Rainy camping trip with Lin",
    "narrative": "The user and Lin cut a camping trip short because of rain. The user said they worked well together.",
    "when": {"precision": "approximate", "description": "October 2026; exact date unknown"},
    "entities": [{"id": "friend-lin", "name": "Lin", "category": "person"}]
  }]
}
JSON

uv run chronicle recall "camping" --namespace tutorial
uv run chronicle history --entity-id friend-lin --namespace tutorial
```

This example writes persistent data in `tutorial`. Replaying the same source and payload is idempotent. To read a full memory and its evidence, use `uv run chronicle show RECORD_ID --namespace tutorial`, replacing `RECORD_ID` with a returned version ID.

For multiple experiences and a supported understanding in one write, see [`examples/experience.json`](examples/experience.json) (Chinese sample content).

## Explore your memory graph

Start the local, read-only **Chronicle Observatory**:

```sh
uv run chronicle ui --namespace personal --port 8765
```

Open <http://127.0.0.1:8765> and use the same namespace as your agent. The viewer connects to your configured Neo4j instance and displays actual memory revisions, entities, sources, and their relationships. It includes an orbiting 3D view, a time-based layout, text and type filters, and a detail panel for narratives and original evidence. Drag to rotate, scroll to zoom, or select nodes from the sidebar. Use Refresh to load newly written memories.

The time slider filters **when records were learned**, not when the underlying experiences happened. It operates on the most recent 200 loaded memory revisions and their connections; it is not a complete historical state replay. Explicit associations use the current association view. Large graphs display a truncation notice. The viewer binds only to `127.0.0.1`, serves no write endpoints, and uses local assets without CDNs or additional frontend dependencies. Keep the command running while using the page; Ctrl+C stops the viewer without stopping Neo4j or MCP.

## Connect an agent

Use the CLI directly from an agent with shell access, or connect the same operations through a **stdio MCP server**. Use a consistent namespace for the user or workspace whose memories should be shared across sessions and agents.

Generate a configuration snippet for your client:

```sh
uv run chronicle config codex --env-file /absolute/path/chronicle/.env --namespace personal
uv run chronicle config claude --env-file /absolute/path/chronicle/.env --namespace personal
uv run chronicle config claude-desktop --env-file /absolute/path/chronicle/.env --namespace personal
```

Run the command for your client and merge its output into that client's configuration. The generated snippet includes the installed executable and absolute `.env` path; the server reads the credentials. Reload the MCP connection after changing configuration or tool definitions.

A useful host instruction is:

> Use Chronicle to retain experiences with their context. Distinguish my explicit statements from your inferences. Recall related memories before answering and inspect sources when needed. Treat uncertain dates and temporary feelings carefully. When answering historical questions, use the same knowledge cutoff for retrieval and detail reads.

MCP initialization also supplies usage guidance. Model-initiated storage and recall depend on the host choosing to call tools. For automatic conversation capture, enable the separate adapter below; connecting MCP alone does not enable capture.

## Automatic conversation capture

The capture core is host-neutral. It accepts `CaptureEvent` JSON containing `origin`, `turn_id`, a `source` (ID, conversation ID, role, content, timezone-aware timestamp), and optional `workspace`/`private` fields. A local SQLite outbox acknowledges receipt before Neo4j processing. A worker archives each turn as a versioned conversation experience, with original speakers and evidence retained. It does **not** automatically turn assistant answers into verified facts or inferred understandings, and needs no additional model API.

```sh
# Any application can submit the same event format.
uv run chronicle capture submit --input event.json --queue data/capture/events.sqlite3
uv run chronicle capture worker --namespace personal --queue data/capture/events.sqlite3
uv run chronicle capture status --queue data/capture/events.sqlite3
```

`accepted`/`queued` means the outbox committed the event; `saved` means the Neo4j archive committed. Status includes pending counts, retry attempts, safe error codes, and saved record IDs. A database outage leaves events queued with bounded backoff. Replay after a commit-before-acknowledgment failure does not create another revision. Run only one worker per queue (enforced with a process lock); the worker currently supports macOS/Linux. For unattended capture, manage it with your OS service manager. `--once` processes one batch and exits.

### Optional Codex adapter

The adapter lives in `chronicle_memory.adapters.codex`; the capture/storage core never imports it. Install hooks from the repository environment:

```sh
uv run python -m chronicle_memory.adapters.codex install \
  --queue "$(pwd)/data/capture/events.sqlite3"
```

This preserves existing hooks and adds `UserPromptSubmit` and `Stop` handlers to `~/.codex/hooks.json`. Review and trust the new definitions in Codex's hook controls; the installer does not modify trust hashes or bypass trust. Start the capture worker with the same absolute queue path and the same namespace as MCP. Installing hooks does not start the worker.

The adapter verifies a user-session transcript header before accepting an event, excluding background/system sessions and unidentified sessions. Desktop validation used runtime `0.167.0-alpha.4.1`. If a new chat's first prompt is absent from `UserPromptSubmit`, `Stop` can recover only typed `UserMessage` items from that same turn, after the activation timestamp. It never imports injected `response_item` instructions, reasoning, or other turns. This narrow transcript-format dependency stays inside the Codex adapter and must be revalidated after host format changes. Existing pre-activation chats are not backfilled; continuing them creates new capturable turns.

The queue's `.codex.json` companion records activation time. `codex-hooks.jsonl` records event IDs and outcomes without message bodies. Explicit private events, `<private>…</private>` text, and supported credential patterns are excluded or redacted before queuing. A user request not to record a turn suppresses its subsequent answer. Disabling the two Chronicle hooks stops new capture; stopping the worker only pauses archiving, preserving pending events. Neither affects Neo4j or MCP availability.

## CLI and MCP reference

The CLI and MCP share input models, business operations, and storage logic. Memory commands accept JSON from a file (`--input file.json`) or standard input (`--input -`) and return JSON.

| Task | CLI | MCP tool |
| --- | --- | --- |
| Write sources, experiences, understandings, and associations atomically | `remember` | `memory_write` |
| Recall a compact index with connection reasons and temporal status | `recall` | `memory_context` |
| Search by keywords, entities/aliases, and optional vectors | `search` | `memory_search` |
| Read full records and original evidence | `show` | `memory_details` |
| Explore graph neighbors | `neighbors` | `memory_neighbors` |
| Inspect memory or entity history | `history` | `memory_history` |
| Read the current time, timezone, and namespace | `now` | `memory_now` |
| List or resolve review candidates | `reviews` / `resolve` | `memory_reviews` / `memory_resolve_review` |
| Query exact state or its timeline | `state` / `timeline` | `memory_state` / `memory_timeline` |
| Process due checks | `tick` | `memory_tick` |
| Extract memories with an optional standalone model | `ingest` | `memory_ingest` |

```sh
uv run chronicle search "camping" --namespace tutorial
uv run chronicle neighbors RECORD_ID --namespace tutorial
uv run chronicle recall "camping" --namespace tutorial \
  --at 2026-10-15T00:00:00+08:00 --known-at 2026-11-12T00:00:00+08:00

# Exact state and review workflows; use your own subject, key, and review ID.
uv run chronicle state "user" "preference"
uv run chronicle timeline "user" "preference"
uv run chronicle reviews
uv run chronicle reviews --kind understanding --limit 50 --offset 0
uv run chronicle resolve REVIEW_ID --input resolution.json
uv run chronicle checks --limit 50 --offset 0
```

`--env-file` and `--namespace` can appear before or after the subcommand. Replace uppercase placeholders before running commands. A review resolution can supply additional evidence and clarified memories; omitting the memory content discards the candidate. Use `chronicle tools` for tool schemas and `chronicle COMMAND --help` for command options.

Success: `{"ok": true, "result": ...}`. Failure: `{"ok": false, "error": {"code": ..., "message": ...}}`. Revision conflicts include `current_record_id`; read the latest version before deciding how to revise it.

| Exit code | Meaning |
| --- | --- |
| `0` | Success |
| `2` | Invalid input, arguments, or command |
| `3` | Source or revision conflict |
| `4` | Storage, initialization, or model service unavailable |
| `130` | Interrupted |

`config`, `tools`, `demo`, and `--help` output configuration snippets, tool definitions, demo results, and help respectively.

`reviews` / `memory_reviews` and `checks` return a page with `items`, `truncated`, and `next_offset` (default 50, maximum 100). Earlier clients expecting a bare list must read `result.items`. Review entries have a `review_kind`: resolve a `candidate` with `memory_resolve_review`; re-evaluate an `understanding` and update its statement/supports with `memory_write`. An understanding's ID is not a candidate review ID. Reviews accept `at` and `known_at`; keep the same time cutoffs when paging historical results.

## Memory model and write contract

| Object | Stores | Key relationships |
| --- | --- | --- |
| `EvidenceSource` | Original message/tool result, role, and timestamp | `EVIDENCED_BY` |
| `Experience` | Narrative, context, entities, and time precision | `INVOLVES`, `REVISES`, `CORRECTS` |
| `Entity` | Stable ID, name, category, and aliases | Connects experiences involving the same entity |
| `Understanding` | Statement, explicit/inferred basis, and validity interval | `SUPPORTED_BY` points to experience versions |
| Explicit association | Explanation, evidence, and validity interval | `RELATED_TO`, `CONTINUES`, `CONTRADICTS`, `CAUSED_BY`, `REVISES` |

A `Memory` node holds the stable logical identity; `HAS_REVISION` connects its versions. Text is stored in node properties, timestamps use native Neo4j DateTime properties, and complete records are also retained as JSON. Shared entities provide recall cues; a causal association requires its own evidence.

- Reuse the stable `id` for the same experience or understanding. Updates submit the full content and reference the latest `record_id` in `replaces`.
- Use `revision_kind=update` for additions or changes and `correct` for an earlier error.
- `when.precision` accepts `exact`, `day`, `range`, `approximate`, or `unknown`. Preserve uncertain dates as descriptions rather than inventing precision.
- `understandings.supports` references experience version IDs or experience IDs from the same write. `basis=explicit` records what the user stated; `inferred` records an agent's interpretation.
- An assistant may write an inferred understanding supported by existing experiences. New experiences require a user message or actual tool result as their source.
- Explicit associations require an explanation, basis, and evidence; use validity times when applicable.
- Reuse `source.id` for retries of the same payload. Use a new source ID when changing saved content.
- Store exact facts, rules, and plans in `memory_write.memories`.
- Entity metadata defaults to `operation=mention`: reuse the canonical name and add aliases. Use `operation=correct` only for an explicit metadata correction, supplying the complete name, category, and alias list. This changes canonical lookup names; original entity descriptions remain in historical experience payloads.
- Assistant sources may create inferred understandings and associations, but cannot mark either as `explicit`.

Exact-state IDs encode the subject/key pair without ambiguous delimiters. Existing state records remain readable by their stored subject/key fields; no bulk ID rewrite is required. Corrections validate both fields against the target record.

Sources, memory revisions, relationships, and checks are committed through Neo4j transactions. Accepted raw messages remain available for retry after a failed processing attempt.

## Time and revisions

Chronicle distinguishes three kinds of time:

| Time | Fields | Question |
| --- | --- | --- |
| Event time | `when.start/end`, precision, description | When did it happen? |
| Knowledge time | Service-generated `recorded_at` | When did Chronicle learn it? |
| Validity time | `valid_from/valid_to` | When does this understanding or association apply? |

`at` selects the event or validity time being queried. `known_at` limits the information available to what had been recorded by that cutoff. For example, a correction recorded in November can change today's account of an October event while leaving an October knowledge view intact. Use the same `at` and `known_at` for search/context and detail reads (`show --at ... --known-at ...`). Details include evidence status and the understanding's review state.

Undated memories are returned separately in `memory_history.undated`. Corrected evidence is surfaced in the status of dependent understandings. Expired rules stop applying; due plans await outcome evidence. Applicability is computed at query time, while a separate worker records due checks.

History filters overlap understanding validity intervals and experience intervals. A `day` without an end spans one day; an exact time without an end is a point. A missing range end does not imply an indefinitely continuing event. Entity history includes understandings citing that entity's experiences. Association history retains earlier versions with `superseded` and `current_version_id`; truncation also accounts for associations.

### Run due checks

```sh
uv run chronicle worker --namespace personal --interval 30

# Or process due checks once, for example from an external scheduler.
uv run chronicle tick --namespace personal
```

The worker processes up to 100 checks per batch, releases the write lock between batches, and continues while a backlog remains. It emits JSON lines when checks are processed and diagnostics to stderr. SIGINT/SIGTERM lets an in-flight database operation finish before shutdown.

CLI queries and the MCP server do not start a worker. Multiple workers in the same namespace coordinate through database locks and processed markers.

A processed check with outcome `conflict` means the check ran, not that the conflict was resolved. It is not repeatedly requeued; state/context queries continue to expose the unresolved conflict. Upcoming context includes currently open plans with future due dates, not just records that become effective in the future.

## Retrieval, concurrency, and optional models

Retrieval combines full-text candidates, entity matches, and optional Neo4j vector search before loading candidate histories and evidence. Results expose `candidate_count` and `candidate_budget`. Large revision or evidence histories can still increase read cost.

- Entity/evidence neighbors resolve the visible revision before sorting and limiting logical memories. `neighbors.truncated` and `recall.related_truncated` signal incomplete expansion.
- Evidence neighbors follow the logical experience across revisions while retaining the exact cited version IDs. Replacing an experience does not silently reattach an understanding to new evidence. Pending understandings appear in related context/attention and in `reviews --kind understanding`.
- Vector indexes are separated by namespace and model and retain historical versions. Retrieval deduplicates logical memories and expands the candidate window within a bounded budget; an exhausted window produces a warning. Current candidate versions are scored separately so old versions do not consume their scoring slots. Use `history` for a specific object's history.
- An episode or understanding found only through another version has `match_scope=historical` and `matched_record_ids`. The returned narrative is the version selected for the query's time view; the matched version is a recall cue, not a claim that its old content is still true. Exact-state answers still require a match against their selected state evidence.
- Chinese keyword retrieval uses bigrams. For short entity names, use an exact-name query or explicit `--subject` to avoid incidental substring matches.
- Writes within a namespace are serialized with Neo4j locks. Stale revisions produce conflicts; different agents' edits are not automatically merged.
- A retrieval fixes a commit sequence and temporal view for its reads. Database operations run in threads, and shutdown waits for started operations to finish.

Optional standalone extraction and embeddings use configurable Chat Completions / Embeddings-compatible services. See [`.env.example`](.env.example) for settings.

```sh
# source.json contains a source payload for the configured extraction model.
uv run chronicle ingest --input source.json

# After configuring embeddings, create or populate the vector indexes.
uv run chronicle reindex
```

When the host agent extracts memories, write them directly with `remember`; a separate extraction model is unnecessary. Model-service failures preserve committed memories, and retrieval reports degradation warnings. Standalone extraction merges keywords, entity matches, and recent conversation sources into at most 20 distinct logical memories, using one read view and their latest writable record IDs (including future-effective versions). Long full-text queries are split into Lucene-safe chunks without discarding the source. Context-retrieval failures are included in ingest failure handling and can be retried with the same source ID.

## Operations and current scope

The local Neo4j installation can be managed with `data/runtime/neo4j-community-5.26.33/bin/neo4j status` and `stop`, using a compatible Java environment. On macOS, run it in the foreground when it is not already running:

```sh
JAVA_HOME=$(/usr/libexec/java_home -v 21) \
  data/runtime/neo4j-community-5.26.33/bin/neo4j console
```

Neo4j authenticates database accounts; `.env` holds the connection credentials. A namespace partitions data but is **not an authorization boundary**. Clients holding database credentials have that account's permissions. The current service is intended for local or controlled environments.

The host model is responsible for interpreting experiences and forming understandings. Periodic autonomous synthesis, long-term compression/forgetting, and automatic entity disambiguation are not implemented. Large-scale performance has not been established. CLI help, some diagnostics, and the detailed testing guide currently remain in Chinese.

## Development

After updating an existing installation, run `uv run chronicle init` to add the state-identity and relationship-property indexes. This is idempotent and preserves data. Reload existing MCP connections to load the new tool schemas and implementation.

Implementation lives in [`src/chronicle_memory/`](src/chronicle_memory/):

| File | Responsibility |
| --- | --- |
| `store.py` | Neo4j transactions, graph, revisions, and evidence |
| `models.py` | Input and temporal constraints |
| `service.py` | Retrieval fusion and related context |
| `cli.py` / `mcp_server.py` / `tools.py` | CLI, MCP, and shared operation dispatch |
| `worker.py` | Due-check loop |
| `state_projection.py` | Temporal projection of exact state |

```sh
uv run pytest -q
```

Tests require a running, initialized Neo4j instance and use isolated namespaces with cleanup. GitHub Actions initializes a dedicated Neo4j service, runs the tests, and builds the package. See the [testing guide](docs/testing.md) (Chinese) for coverage and host-agent acceptance steps; protocol checks and model conversation quality are evaluated separately.

## License

[Apache License 2.0](LICENSE).
