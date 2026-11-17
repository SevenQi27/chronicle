package main

import (
	"context"
	_ "embed"
	"encoding/json"
	"errors"
	"log"
	"sync"
	"time"

	"github.com/SevenQi27/chronicle/internal/refine"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

//go:embed tool-schemas.json
var toolSchemas []byte

const instructions = "SQLite 模式。使用 memory_write 保存带来源的经历或认识，认识必须引用支撑经历。" +
	"使用 memory_search 或 memory_context 检索摘要，memory_details 查阅来源和支撑经历版本。" +
	"使用 memory_visibility 撤回或恢复记忆。" +
	"更正时先查询当前记录，沿用记忆 ID、为新原话提供新来源 ID，并用 replaces 引用当前记录的 record_id。"

type normalizer interface{ normalize() error }

func handleTool[In any, Ptr interface {
	*In
	normalizer
}, Out any](
	defaults In, run func(context.Context, In) (Out, error),
) mcp.ToolHandler {
	return func(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
		input := defaults
		raw := req.Params.Arguments
		if len(raw) == 0 {
			raw = json.RawMessage("{}")
		}
		err := decodeStrict(raw, &input)
		if err == nil {
			err = Ptr(&input).normalize()
		}
		var envelope object
		if err == nil {
			var result Out
			result, err = run(ctx, input)
			if err == nil {
				envelope = success(result)
			}
		}
		if err != nil {
			envelope = failure(err)
		}
		return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: jsonString(envelope)}},
			StructuredContent: envelope, IsError: err != nil}, nil
	}
}

func memoryServer(store *memoryStore) (*mcp.Server, error) {
	var schemas map[string]json.RawMessage
	if err := json.Unmarshal(toolSchemas, &schemas); err != nil {
		return nil, err
	}
	server := mcp.NewServer(&mcp.Implementation{Name: "chronicle-memory", Version: version},
		&mcp.ServerOptions{Instructions: instructions})
	closedWorld := false
	for _, tool := range []struct {
		name, description, schema string
		readOnly                  bool
		handler                   mcp.ToolHandler
	}{
		{"memory_write", "保存带来源的经历或认识。认识的 supports 必须引用已有经历。更正时沿用记忆 ID，replaces 填当前版本的 record_id。", "memory_write", false, handleTool(writeRequest{}, store.write)},
		{"memory_search", "按关键词、实体名称和已保存的别名检索经历及认识。可传 alternatives。", "memory_search", true, handleTool(recallQuery{Limit: 8}, store.recall)},
		{"memory_context", "检索相关记忆的短摘要与 record_id，按需使用 memory_details。", "memory_search", true, handleTool(recallQuery{Limit: 8}, store.recall)},
		{"memory_details", "按版本 ID 读取记忆、支撑经历与来源。显式传 include_withdrawn=true 可查看已撤回的版本。", "memory_details", true, handleTool(detailQuery{SourceMode: "full"}, store.details)},
		{"memory_visibility", "withdraw 撤回一条逻辑记忆的所有版本，restore 恢复可见性。保留原文和历史。", "memory_visibility", false, handleTool(visibilityRequest{}, store.visibility)},
	} {
		server.AddTool(&mcp.Tool{Name: tool.name, Description: tool.description, InputSchema: schemas[tool.schema],
			Annotations: &mcp.ToolAnnotations{ReadOnlyHint: tool.readOnly, OpenWorldHint: &closedWorld}}, tool.handler)
	}
	return server, nil
}

func serveMCP(ctx context.Context, path, namespace, queue string, transport mcp.Transport, model refine.Model) error {
	store, err := openStore(ctx, path, namespace)
	if err != nil {
		return err
	}
	defer store.db.Close()
	if model != nil {
		if err := store.enableRefinement(ctx); err != nil {
			return err
		}
	}
	server, err := memoryServer(store)
	if err != nil {
		return err
	}
	if queue == "" {
		queue = defaultQueue(path, namespace)
	}
	workerContext, cancel := context.WithCancel(ctx)
	var workers sync.WaitGroup
	startMCPWorker(workerContext, &workers, "Capture queue unavailable", func(ctx context.Context) error {
		return drainCapture(ctx, queue, store)
	})
	if model != nil {
		startMCPWorker(workerContext, &workers, "Refinement failed", func(ctx context.Context) error {
			return store.drainRefinement(ctx, model)
		})
	}
	err = server.Run(ctx, transport)
	cancel()
	workers.Wait()
	if errors.Is(err, context.Canceled) {
		return nil
	}
	return err
}

func startMCPWorker(ctx context.Context, workers *sync.WaitGroup, message string, process func(context.Context) error) {
	workers.Add(1)
	go func() {
		defer workers.Done()
		ticker := time.NewTicker(2 * time.Second)
		defer ticker.Stop()
		for ctx.Err() == nil {
			if err := process(ctx); err != nil && ctx.Err() == nil {
				log.Printf("%s: %T", message, err)
			}
			select {
			case <-ctx.Done():
			case <-ticker.C:
			}
		}
	}()
}
