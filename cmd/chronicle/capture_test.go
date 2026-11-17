package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/SevenQi27/chronicle/internal/refine"
	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func testQueue(t *testing.T, path string) *captureQueue {
	t.Helper()
	q, err := openQueue(t.Context(), path)
	queue := must(t, q, err)
	t.Cleanup(func() { _ = queue.db.Close() })
	return queue
}

func sampleEvent(role, content string) captureEvent {
	at := nowUTC()
	if role == "assistant" {
		at = at.Add(time.Microsecond)
	}
	return captureEvent{
		Origin: "codex", TurnID: "turn", Workspace: "/project",
		Source: source{ID: role, Role: role, Content: content, OccurredAt: dateTime{at}, ConversationID: "chat"},
	}
}

func waitForQueue(t *testing.T, q *captureQueue) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		result, err := q.status(t.Context())
		if must(t, result, err)["pending"] == 0 {
			return
		}
		time.Sleep(100 * time.Millisecond)
	}
	t.Fatal("capture queue did not drain")
}

func TestMCPDrainsCaptureAfterReconnect(t *testing.T) {
	for _, custom := range []bool{false, true} {
		name := "default queue"
		if custom {
			name = "explicit queue"
		}
		t.Run(name, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "archive.sqlite3")
			queuePath, flagValue := defaultQueue(path, "project"), ""
			if custom {
				queuePath = filepath.Join(filepath.Dir(path), "events.sqlite3")
				flagValue = queuePath
			}
			s := connectMCP(t, path, "project", flagValue)
			equal(t, searchIDs(t, s, object{"query": "portable_capture_probe"}), []string{})
			check(t, s.Close())
			queue := testQueue(t, queuePath)
			for _, event := range []captureEvent{sampleEvent("user", "portable_capture_probe"), sampleEvent("assistant", "已收到")} {
				output, err := queue.submit(t.Context(), event, false)
				equal(t, must(t, output, err)["status"], "queued")
				output, err = queue.submit(t.Context(), event, false)
				equal(t, must(t, output, err)["duplicate"], true)
			}
			status, err := queue.status(t.Context())
			equal(t, must(t, status, err)["pending"], 2)
			s = connectMCP(t, path, "project", flagValue)
			waitForQueue(t, queue)
			ids := searchIDs(t, s, object{"query": "portable_capture_probe", "filters": object{"origin": "codex", "conversation_id": "codex:chat"}})
			equal(t, len(ids), 1)
			details := call(t, s, "memory_details", object{"event_ids": ids}, "").([]any)
			sources := details[0].(map[string]any)["sources"].([]any)
			equal(t, len(sources), 3)
			equal(t, sources[1].(map[string]any)["content"], "portable_capture_probe")
			equal(t, sources[2].(map[string]any)["content"], "已收到")
			check(t, s.Close())
			s = connectMCP(t, path, "project", flagValue)
			defer s.Close()
			equal(t, searchIDs(t, s, object{"query": "portable_capture_probe"}), ids)
			store := testStore(t, path, "project")
			var count int
			check(t, store.db.QueryRow("SELECT count(*) FROM chronicle_experience").Scan(&count))
			equal(t, count, 1)
		})
	}
}

func TestMCPRefinesCompleteTurnAndReplaysWithoutDuplicate(t *testing.T) {
	const original = "确定了，星桥项目的月度报表统一使用 CSV。"
	var calls atomic.Int32
	provider := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if calls.Add(1) == 1 {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		var request struct {
			Messages []struct {
				Content string `json:"content"`
			} `json:"messages"`
		}
		if err := json.NewDecoder(r.Body).Decode(&request); err != nil || len(request.Messages) != 2 {
			t.Errorf("unexpected model request: %v", err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		var input refine.Input
		if err := json.Unmarshal([]byte(request.Messages[1].Content), &input); err != nil || len(input.Evidence) != 2 || input.Evidence[0].Content != original {
			t.Errorf("unexpected turn evidence: %#v (%v)", input.Evidence, err)
			w.WriteHeader(http.StatusBadRequest)
			return
		}
		candidate := refine.Candidate{Action: "create", Basis: "explicit", Title: "星桥报表格式",
			Statement: "星桥项目的月度报表统一使用 CSV。", SourceIDs: []string{input.Evidence[0].SourceID}}
		_ = json.NewEncoder(w).Encode(object{"choices": []object{{"finish_reason": "stop",
			"message": object{"content": jsonString(object{"candidates": []refine.Candidate{candidate}})}}}})
	}))
	defer provider.Close()
	dir := t.TempDir()
	path := filepath.Join(dir, "memory.sqlite3")
	queue := testQueue(t, filepath.Join(dir, "events.sqlite3"))
	options := []string{"--refine-base-url", provider.URL + "/v1", "--refine-model", "test"}
	start := func() *mcp.ClientSession { return connectMCP(t, path, "refine-test", queue.path, options...) }
	searchRefined := func(session *mcp.ClientSession) []string {
		result := call(t, session, "memory_search", object{"query": "星桥报表 CSV"}, "").(map[string]any)
		var ids []string
		for _, hit := range result["results"].([]any) {
			item := hit.(map[string]any)
			if item["record_kind"] == "understanding" {
				ids = append(ids, item["record_id"].(string))
			}
		}
		return ids
	}
	s := start()
	t.Cleanup(func() { _ = s.Close() })
	_, err := queue.submit(t.Context(), sampleEvent("user", original), false)
	check(t, err)
	waitForQueue(t, queue)
	store := testStore(t, path, "refine-test")
	var jobs int
	check(t, store.db.QueryRow("SELECT count(*) FROM chronicle_refinement").Scan(&jobs))
	equal(t, jobs, 0)
	equal(t, calls.Load(), int32(0))
	_, err = queue.submit(t.Context(), sampleEvent("assistant", "收到。"), false)
	check(t, err)
	var ids []string
	for deadline := time.Now().Add(12 * time.Second); len(ids) == 0 && time.Now().Before(deadline); {
		time.Sleep(100 * time.Millisecond)
		ids = searchRefined(s)
	}
	equal(t, len(ids), 1)
	equal(t, calls.Load(), int32(2))
	detail := call(t, s, "memory_details", object{"event_ids": ids}, "").([]any)[0].(map[string]any)
	versionIDs := detail["support_record_ids"].([]any)
	equal(t, len(versionIDs), 1)
	experience := call(t, s, "memory_details", object{"event_ids": versionIDs}, "").([]any)[0].(map[string]any)
	equal(t, experience["sources"].([]any)[1].(map[string]any)["content"], original)
	check(t, s.Close())
	// Replay messages as if the archive committed just before queue acknowledgement.
	_, err = queue.db.Exec("UPDATE events SET status='queued'")
	check(t, err)
	s = start()
	waitForQueue(t, queue)
	equal(t, searchRefined(s), ids)
	check(t, store.db.QueryRow("SELECT count(*) FROM chronicle_refinement").Scan(&jobs))
	equal(t, jobs, 1)
	var attempts, done int
	check(t, store.db.QueryRow("SELECT attempts, done FROM chronicle_refinement").Scan(&attempts, &done))
	equal(t, attempts, 1)
	equal(t, done, 1)
	equal(t, calls.Load(), int32(2))
}

func TestCaptureRetryAndReplay(t *testing.T) {
	root := t.TempDir()
	ctx := t.Context()
	queue := testQueue(t, filepath.Join(root, "events.sqlite3"))
	_, err := queue.submit(ctx, sampleEvent("user", "会后回放"), false)
	check(t, err)
	progress, err := processPending(ctx, queue, nil, root, "project")
	equal(t, must(t, progress, err), captureProgress{Failed: 1})
	status, err := queue.status(ctx)
	equal(t, must(t, status, err)["pending"], 1)
	_, err = queue.db.Exec("UPDATE events SET retry_at=0")
	check(t, err)
	store := testStore(t, filepath.Join(root, "db.sqlite3"), "project")
	progress, err = processPending(ctx, queue, store, store.path, store.namespace)
	equal(t, must(t, progress, err), captureProgress{Saved: 1})
	_, err = queue.submit(ctx, sampleEvent("assistant", "回复原文"), false)
	check(t, err)
	ready, err := queue.ready(ctx)
	rows := must(t, ready, err)
	events, err := queue.turn(ctx, rows[0].turn)
	check(t, err)
	id, err := store.archiveTurn(ctx, rows[0].turn, events)
	check(t, err)
	progress, err = processPending(ctx, queue, store, store.path, store.namespace)
	equal(t, must(t, progress, err), captureProgress{Saved: 1})
	detail, err := store.details(ctx, detailQuery{EventIDs: []string{id}, SourceMode: "full"})
	value := must(t, detail, err)[0]
	equal(t, value["source_count"], 4)
	var count int
	check(t, store.db.QueryRow("SELECT count(*) FROM chronicle_experience").Scan(&count))
	equal(t, count, 2)
}

func TestCaptureLockAndPrivacy(t *testing.T) {
	queue := testQueue(t, filepath.Join(t.TempDir(), "capture.sqlite3"))
	ctx := t.Context()
	check(t, queue.withWorkerLock(ctx, func() error {
		err := queue.withWorkerLock(ctx, func() error { return nil })
		if !errors.Is(err, workerAlreadyRunning) {
			t.Fatalf("second worker lock: %v", err)
		}
		for _, event := range []captureEvent{sampleEvent("user", "不要记录这段内容"), sampleEvent("assistant", "好")} {
			result, err := queue.submit(ctx, event, false)
			equal(t, must(t, result, err)["status"], "private")
		}
		return nil
	}))
	check(t, queue.withWorkerLock(ctx, func() error { return nil }))
	equal(t, redact("保留 <private>隐藏</private> password=example"), "保留 [private] password=[credential]")
	var present int
	check(t, queue.db.QueryRow("SELECT count(*) FROM events WHERE body IS NOT NULL").Scan(&present))
	equal(t, present, 0)
}

func TestCodexHookUpgradeAndPromptRecovery(t *testing.T) {
	root := t.TempDir()
	queue := testQueue(t, filepath.Join(root, "events.sqlite3"))
	executable := filepath.Join(root, "Chronicle tool")
	cfg := filepath.Join(root, "hooks.json")
	other := []object{{"hooks": []object{{"type": "command", "command": "existing-check"}}}}
	oldCommand := "python -m chronicle_memory.adapters.codex hook --queue " + unixQuote(queue.path)
	existing := object{"hooks": object{"PreToolUse": other, "Stop": []object{{"hooks": []object{{
		"type": "command", "command": oldCommand, "async": true,
	}}}}}}
	check(t, os.WriteFile(cfg, []byte(jsonString(existing)), 0600))
	plan, err := installCodexHooks(root, queue.path, executable)
	equal(t, must(t, plan, err)["requires_hook_trust"], true)
	result, err := os.ReadFile(cfg)
	content := string(must(t, result, err))
	if strings.Contains(content, "chronicle_memory.adapters.codex") || strings.Contains(content, `"async"`) ||
		!strings.Contains(content, "existing-check") {
		t.Fatal("hook installation changed unrelated commands or kept the old command")
	}
	_, err = installCodexHooks(root, queue.path, executable)
	check(t, err)
	newConfig, err := os.ReadFile(cfg)
	equal(t, string(must(t, newConfig, err)), content)
	transcript := filepath.Join(root, "chat.jsonl")
	msg := func(turn, body string, at time.Time) object {
		return object{
			"type": "event_msg", "timestamp": stamp(at), "payload": object{"type": "item_completed", "turn_id": turn, "thread_id": "chat",
				"item": object{"type": "UserMessage", "content": []object{{"type": "text", "text": body}}}},
		}
	}
	rows := []object{
		{"type": "session_meta", "payload": object{"id": "chat", "thread_source": "user", "cwd": root}},
		msg("old", "other turn", nowUTC()),
		msg("turn", "before activation", nowUTC().Add(-24*time.Hour)),
		msg("turn", "用户原话", nowUTC().Add(time.Microsecond)),
		{"type": "response_item", "payload": object{"role": "user", "content": "not a user event"}},
	}
	var jsonl bytes.Buffer
	for _, row := range rows {
		jsonl.WriteString(jsonString(row) + "\n")
	}
	check(t, os.WriteFile(transcript, jsonl.Bytes(), 0600))
	payload := codexPayload{Event: "Stop", Session: "chat", Turn: "turn", Transcript: transcript, LastMessage: "助手原话"}
	captured, err := captureCodex(context.Background(), queue, payload)
	equal(t, must(t, captured, err)["recovered_prompts"], 1)
	captured, err = captureCodex(t.Context(), queue, payload)
	equal(t, must(t, captured, err)["duplicate"], true)
	events, err := queue.turn(t.Context(), captureTurnKey("codex", "chat", "turn"))
	equal(t, len(must(t, events, err)), 2)
	equal(t, events[0].Source.Content, "用户原话")
	equal(t, events[1].Source.Content, "助手原话")
	rows[0]["payload"].(map[string]any)["thread_source"] = "subagent"
	jsonl.Reset()
	for _, row := range rows {
		jsonl.WriteString(jsonString(row) + "\n")
	}
	check(t, os.WriteFile(transcript, jsonl.Bytes(), 0600))
	captured, err = captureCodex(t.Context(), queue, payload)
	equal(t, must(t, captured, err)["skipped"], "not_a_verified_user_session")
}
