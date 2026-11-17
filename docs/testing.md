# SQLite 测试

```sh
go test ./...
go build ./...
```

测试使用临时 SQLite 文件。MCP 端到端测试通过真实 stdio 握手验证写入、索引召回、更正、历史查询和重启后读回。另有旧库索引及到期时间迁移、Codex hook、采集断线重连与去重，以及图谱 HTTP 入口的覆盖。

单独运行 MCP 和采集断线重连测试：

```sh
go test ./cmd/chronicle -run 'TestMCP(StdioLifecycle|DrainsCaptureAfterReconnect)$'
```
