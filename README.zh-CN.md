# Chronicle Memory

Chronicle 是一个本地 MCP 记忆服务。它用 SQLite 保存经历和原始证据，更正时保留历史版本。

[English](README.md)

## 启动

需要 Python 3.11 或更新版本（SQLite 支持 FTS5）和 uv。在仓库目录运行：

```sh
uv sync --locked
uv run chronicle mcp
```

在 MCP 宿主中，把已安装的 `chronicle` 配为 stdio 服务，启动参数填 `mcp`。首次启动会自动建库和建索引，默认文件为 `~/.local/share/chronicle/memory.sqlite3`。

也可以指定库文件和分区：

```sh
chronicle mcp --sqlite /absolute/path/memory.sqlite3 --namespace personal
```

重启时沿用同一文件路径和分区即可读回已有记忆。已有 Chronicle SQLite 库会在启动时自动补建全文索引。

运行 `chronicle ui` 会沿用当前 Chronicle MCP 配置中的库路径和分区。显式指定的 `--sqlite` 或 `--namespace` 优先。浏览器打开 <http://127.0.0.1:8765/> 查看 3D 图谱。

## MCP 工具

| 工具 | 用途 |
| --- | --- |
| `memory_write` | 保存带原始证据的经历与认识。认识需在 `supports` 中引用当前分区的经历 ID 或版本 ID。 |
| `memory_search`、`memory_context` | 按关键词检索经历与认识，也可按来源平台、工作区、会话或证据来源筛选。 |
| `memory_details` | 按 `record_id` 查看版本与来源。读取认识后，可继续按 `support_record_ids` 查看支撑经历及其原文。 |
| `memory_visibility` | 对 `memory_ids` 执行 `withdraw`（撤回）或 `restore`（恢复）。可传记忆 ID 或任意版本 ID。 |

更正时沿用记忆 ID，给新原话分配新来源 ID，并用 `replaces` 引用当前版本的 `record_id`。查询可用 `known_at` 指定当时已获知的信息，用 `at` 指定生效时间。认识引用经历 ID 时，保存时会解析为当时的具体版本。使用 `source_mode=summary` 可省略来源正文。

关键词、别名和显式传入的 `alternatives` 都能参与召回，无需配置模型服务。

撤回会在默认搜索、详情和可视化页面中隐藏该记忆的全部版本，按历史时间查询时也遵守当前撤回状态。原文和版本继续保留。显式传入 `memory_details.include_withdrawn=true` 可按版本 ID 查看；恢复后可照常检索。

运行 `uv run python -m pytest -q` 验证。端到端示例见[测试说明](docs/testing.md)。
