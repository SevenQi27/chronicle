# Chronicle Memory

Chronicle 是一个本地 MCP 记忆服务。它用 SQLite 保存经历和原始证据，更正时保留历史版本。

[English](README.md)

## 启动

在仓库目录使用 Go 1.24 或更高版本构建。可执行文件内置 SQLite 和 FTS5 全文索引。

```sh
go install ./cmd/chronicle
```

在 MCP 宿主中，用已安装的 `chronicle` 可执行文件的绝对路径配置 stdio 服务，启动参数填 `mcp`。首次启动会自动建库和建索引，默认文件为 `~/.local/share/chronicle/memory.sqlite3`。

也可以指定库文件和分区：

```sh
chronicle mcp --sqlite /absolute/path/memory.sqlite3 --namespace personal
```

切换现有安装时，沿用 MCP 配置里的 `--sqlite`、`--namespace` 和已有的 `--capture-queue` 参数。已有 Chronicle SQLite 库会在启动时自动补建全文索引。

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

## 自动采集对话

Chronicle MCP 在连接期间自动归档当前库和分区的队列。其他宿主可用 `chronicle capture submit --input event.json` 入队，参数 `--sqlite` 和 `--namespace` 与 MCP 对齐。事件包含 `origin`、`turn_id`、`source` 和可选的 `workspace`、`private`，其中 `source` 记录消息 ID、会话 ID、角色、正文和带时区的时间。

### Codex 适配器

在仓库目录安装可选的 Codex 适配器。

```sh
chronicle capture setup-codex
```

安装器沿用 Codex 中 Chronicle MCP 的库路径和分区，保留现有 hook。按 Codex 的 hook 信任流程审核 `UserPromptSubmit` 和 `Stop` 后，新对话会进入本地队列。MCP 未连接时，消息留在队列，连接后继续归档。

`chronicle capture status` 可查看队列和重试状态，`saved` 表示已写入记忆库。每轮对话按版本归档为经历，用 `memory_search` 搜索后可通过 `memory_details` 查看角色和原文。标记为私密的内容会在排队前排除或脱敏，采集无需配置模型。

需要在 MCP 未连接时持续归档，可运行 `chronicle capture worker`，加 `--once` 可只处理一批。若原来使用了自定义队列，MCP 用 `--capture-queue PATH`，采集命令用 `--queue PATH` 指向同一文件。

在 `chronicle mcp` 启动时同时指定 `--refine-base-url` 和 `--refine-model`，可启用新对话的自动提炼。需要鉴权时，通过 `CHRONICLE_REFINE_API_KEY` 环境变量提供密钥。完整轮次归档后，MCP 将该轮原文和相关认识发送给所配置的模型，生成的认识可通过 `support_record_ids` 追溯到原文。重试和重启沿用同一个归档版本的提炼任务。

## 测试

运行 `go test ./...` 验证。端到端测试见[测试说明](docs/testing.md)。
