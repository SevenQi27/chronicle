package main

import (
	"bytes"
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"
)

func must[T any](t *testing.T, value T, err error) T {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
	return value
}

func check(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatal(err)
	}
}

func equal(t *testing.T, got, want any) {
	t.Helper()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("got %#v, want %#v", got, want)
	}
}

func testStore(t *testing.T, path, namespace string) *memoryStore {
	t.Helper()
	s, err := openStore(t.Context(), path, namespace)
	store := must(t, s, err)
	t.Cleanup(func() { _ = store.db.Close() })
	return store
}

func parseDate(t *testing.T, data string) *dateTime {
	t.Helper()
	parsed, err := time.Parse(time.RFC3339Nano, data)
	return &dateTime{must(t, parsed, err)}
}

func TestChronicleProcess(t *testing.T) {
	if os.Getenv("CHRONICLE_TEST_PROCESS") != "1" {
		return
	}
	for i, arg := range os.Args {
		if arg == "--" {
			if err := run(context.Background(), os.Args[i+1:], os.Stdin, os.Stdout); err != nil {
				t.Log(err)
				os.Exit(1)
			}
			os.Exit(0)
		}
	}
	os.Exit(2)
}

func connectMCP(t *testing.T, path, namespace, queue string, opts ...string) *mcp.ClientSession {
	t.Helper()
	args := []string{"-test.run=^TestChronicleProcess$", "--", "mcp", "--sqlite", path, "--namespace", namespace}
	if queue != "" {
		args = append(args, "--capture-queue", queue)
	}
	args = append(args, opts...)
	command := exec.Command(os.Args[0], args...)
	command.Env = append(os.Environ(), "CHRONICLE_TEST_PROCESS=1")
	client := mcp.NewClient(&mcp.Implementation{Name: "chronicle-test", Version: "1"}, nil)
	ctx, cancel := context.WithTimeout(t.Context(), 15*time.Second)
	defer cancel()
	session, err := client.Connect(ctx, &mcp.CommandTransport{Command: command}, nil)
	return must(t, session, err)
}

func call(t *testing.T, session *mcp.ClientSession, tool string, args object, code string) any {
	t.Helper()
	ctx, cancel := context.WithTimeout(t.Context(), 12*time.Second)
	defer cancel()
	response, err := session.CallTool(ctx, &mcp.CallToolParams{Name: tool, Arguments: args})
	r := must(t, response, err)
	body := r.StructuredContent.(map[string]any)
	equal(t, r.IsError, code != "")
	equal(t, body["ok"], code == "")
	if code != "" {
		failure := body["error"].(map[string]any)
		equal(t, failure["code"], code)
		return failure
	}
	return body["result"]
}

func experienceArgs(id, title, logical string) object {
	return object{
		"source":  object{"id": id, "content": title, "conversation_id": "chat", "occurred_at": "2026-01-01T00:00:00Z"},
		"context": object{"origin": "codex", "workspace": "/project"},
		"experiences": []object{{"id": logical, "title": "极光评审", "narrative": title,
			"entities": []object{{"id": "aurora", "name": "极光评审", "aliases": []string{"北辰碰头"}}}}},
	}
}

func recordID(result any) string { return result.(map[string]any)["record_ids"].([]any)[0].(string) }

func searchIDs(t *testing.T, s *mcp.ClientSession, query object) []string {
	rows := call(t, s, "memory_search", query, "").(map[string]any)["results"].([]any)
	ids := make([]string, len(rows))
	for i, row := range rows {
		ids[i] = row.(map[string]any)["record_id"].(string)
	}
	return ids
}

func TestMCPStdioLifecycle(t *testing.T) {
	path := filepath.Join(t.TempDir(), "memory archive.sqlite3")
	s := connectMCP(t, path, "test", "")
	list, err := s.ListTools(t.Context(), nil)
	tools := must(t, list, err).Tools
	equal(t, len(tools), 5)
	firstArgs := experienceArgs("source-one", "极光评审在三楼", "meeting")
	wrote := call(t, s, "memory_write", firstArgs, "").(map[string]any)
	first := recordID(wrote)
	equal(t, call(t, s, "memory_write", firstArgs, ""), wrote)
	equal(t, searchIDs(t, s, object{"query": "北辰碰头", "filters": object{"workspace": "/project", "origin": "codex", "conversation_id": "chat"}}), []string{first})
	equal(t, searchIDs(t, s, object{"query": "土星环", "alternatives": []string{"北辰碰头"}, "filters": object{"source_id": "source-one"}}), []string{first})
	equal(t, searchIDs(t, s, object{"query": "三楼", "filters": object{"workspace": "/other"}}), []string{})
	args := experienceArgs("source-two", "极光评审改在五楼", "meeting")
	args["experiences"].([]object)[0]["replaces"] = first
	args["experiences"].([]object)[0]["revision_kind"] = "correct"
	second := recordID(call(t, s, "memory_write", args, ""))
	equal(t, searchIDs(t, s, object{"query": "五楼"}), []string{second})
	equal(t, searchIDs(t, s, object{"query": "三楼"}), []string{})
	equal(t, searchIDs(t, s, object{"query": "三楼", "known_at": wrote["recorded_at"]}), []string{first})
	stale := experienceArgs("source-stale", "极光评审在六楼", "meeting")
	stale["experiences"].([]object)[0]["replaces"] = first
	failure := call(t, s, "memory_write", stale, "version_conflict").(map[string]any)
	equal(t, failure["current_record_id"], second)
	call(t, s, "memory_visibility", object{"action": "withdraw", "memory_ids": []string{first}}, "")
	equal(t, searchIDs(t, s, object{"query": "三楼", "known_at": wrote["recorded_at"]}), []string{})
	equal(t, call(t, s, "memory_details", object{"event_ids": []string{first, second}}, ""), []any{})
	check(t, s.Close())

	s = connectMCP(t, path, "test", "")
	defer s.Close()
	equal(t, searchIDs(t, s, object{"query": "五楼"}), []string{})
	revisions := call(t, s, "memory_details", object{"event_ids": []string{second}, "include_withdrawn": true},
		"").([]any)
	detail := revisions[0].(map[string]any)
	equal(t, detail["withdrawn"], true)
	equal(t, detail["source_count"], float64(2))
	call(t, s, "memory_visibility", object{"action": "restore", "memory_ids": []string{"meeting"}}, "")
	equal(t, searchIDs(t, s, object{"query": "五楼"}), []string{second})
	call(t, s, "memory_write", object{"source": object{"id": "bad", "content": "不合法", "occurred_at": "2026-01-01T00:00:00Z"},
		"understandings": []object{{"id": "bad", "title": "不合法", "statement": "不存在", "basis": "inferred", "supports": []string{"other"}}}}, "invalid_arguments")
	future := experienceArgs("source-three", "极光评审在七楼", "meeting")
	future["experiences"].([]object)[0]["replaces"] = second
	future["experiences"].([]object)[0]["when"] = object{"start": "2099-01-01T00:00:00Z", "precision": "exact"}
	third := recordID(call(t, s, "memory_write", future, ""))
	equal(t, searchIDs(t, s, object{"query": "七楼"}), []string{})
	equal(t, searchIDs(t, s, object{"query": "七楼", "at": "2099-01-01T00:00:00Z"}), []string{third})
}

func TestLegacyIndexAndExpiryMigration(t *testing.T) {
	for _, expiry := range []string{"2026-11-16T09:00:00+08:00", "2026-11-16T01:00:00Z", "2026-11-15T20:00:00-05:00", ""} {
		t.Run(expiry, func(t *testing.T) {
			path := filepath.Join(t.TempDir(), "old.sqlite3")
			db, err := sqliteDB(path)
			database := must(t, db, err)
			legacy := strings.Replace(schema, "record_kind TEXT NOT NULL DEFAULT 'experience', valid_to TEXT,", "record_kind TEXT NOT NULL DEFAULT 'experience',", 1)
			_, err = database.Exec(legacy)
			check(t, err)
			_, err = database.Exec("INSERT INTO chronicle_source VALUES ('test','s1','{}','{}','{}')")
			check(t, err)
			body := object{"id": "insight", "title": "会议安排", "statement": "议题尚未确认", "basis": "inferred", "valid_to": nil}
			if expiry != "" {
				body["valid_to"] = expiry
			}
			content := jsonString(body)
			_, err = database.Exec(`
INSERT INTO chronicle_experience(namespace,record_id,logical_id,source_id,recorded_at,body,terms,conversation_id,evidence_ids,record_kind)
VALUES('test','version-1','insight','s1','2026-11-15T00:00:00.000000+00:00',?,?,'default','["s1"]','understanding')`,
				content, jsonString([]string{"会议", "安排"}))
			check(t, err)
			check(t, database.Close())
			store := testStore(t, path, "test")
			for i, at := range []string{"2026-11-16T00:59:59.999999Z", "2026-11-16T01:00:00Z", "2026-11-16T01:00:00.000001Z"} {
				rows, err := store.recall(t.Context(), recallQuery{Query: "会议安排", Limit: 8, At: parseDate(t, at),
					KnownAt: parseDate(t, "2026-11-17T00:00:00Z")})
				results := must(t, rows, err)["results"].([]object)
				want := 0
				if i == 0 || expiry == "" {
					want = 1
				}
				equal(t, len(results), want)
			}
			var normalized sql.NullString
			var unchanged string
			check(t, store.db.QueryRow("SELECT valid_to,body FROM chronicle_experience WHERE record_id='version-1'").Scan(&normalized, &unchanged))
			equal(t, unchanged, content)
			if expiry != "" {
				equal(t, normalized.String, "2026-11-16T01:00:00.000000+00:00")
			} else {
				equal(t, normalized.Valid, false)
			}
		})
	}
}

func TestGraphAndPortConflict(t *testing.T) {
	path := filepath.Join(t.TempDir(), "ui.sqlite3")
	store := testStore(t, path, "personal")
	ctx := t.Context()
	write := func(i int, previous *string) string {
		item := experience{ID: "meeting", Title: "会议安排", Narrative: "会议安排", Replaces: previous}
		if previous != nil {
			item.RevisionKind = "correct"
		}
		result, err := store.write(ctx, writeRequest{
			Source:      source{ID: []string{"msg-1", "msg-2"}[i], Content: "原始证据", OccurredAt: dateTime{nowUTC()}},
			Experiences: []experience{item},
		})
		return must(t, result, err)["record_ids"].([]string)[0]
	}
	first := write(0, nil)
	second := write(1, &first)
	handler := graphHandler(store, 8765)
	for path, status := range map[string]int{"/": 200, "/graph": 200, "/graph/app.js": 200, "/graph/style.css": 200,
		"/api/graph": 200, "/ledger": 404, "/api/ledger": 404, "/app.js": 404, "/style.css": 404} {
		request := httptest.NewRequest("GET", "http://127.0.0.1:8765"+path, nil)
		response := httptest.NewRecorder()
		handler.ServeHTTP(response, request)
		equal(t, response.Code, status)
		if path == "/" {
			if !strings.Contains(response.Body.String(), `<canvas id="graph"`) {
				t.Fatal("graph canvas missing")
			}
		}
	}
	r := httptest.NewRecorder()
	handler.ServeHTTP(r, httptest.NewRequest("GET", "http://evil.example/api/graph", nil))
	equal(t, r.Code, http.StatusForbidden)
	data, err := store.graphSnapshot(ctx)
	equal(t, must(t, data, err)["total_records"], 2)
	var correction, sourceEdge bool
	for _, edge := range data["edges"].([]object) {
		correction = correction || edge["from"] == second && edge["to"] == first && edge["kind"] == "CORRECTS"
		sourceEdge = sourceEdge || edge["from"] == first && edge["to"] == "source:msg-1" && edge["kind"] == "EVIDENCED_BY"
	}
	if !correction || !sourceEdge {
		t.Fatal("missing graph edges")
	}
	l, err := net.Listen("tcp4", "127.0.0.1:0")
	listener := must(t, l, err)
	defer listener.Close()
	err = serveUI(ctx, path, "personal", listener.Addr().(*net.TCPAddr).Port, &bytes.Buffer{})
	var api *apiError
	if !errors.As(err, &api) || api.code != "port_in_use" {
		t.Fatalf("unexpected occupied-port error: %v", err)
	}
}

func TestMCPConfigRelativePathsAndOverrides(t *testing.T) {
	dir := t.TempDir()
	config := mcpConfig{CWD: dir, Args: []string{"mcp", "--sqlite", "db name.sqlite3", "--namespace=project", "--capture-queue=q.sqlite3"}}
	target, err := configuredTarget(config)
	equal(t, must(t, target, err), targetOptions{filepath.Join(dir, "db name.sqlite3"), "project", filepath.Join(dir, "q.sqlite3")})
	t.Setenv("CODEX_HOME", dir)
	t.Setenv("PATH", "")
	data, err := json.Marshal(config.Args)
	check(t, err)
	check(t, os.WriteFile(filepath.Join(dir, "config.toml"), []byte("[mcp_servers.chronicle]\nargs = "+string(data)+"\n"), 0600))
	wanted := targetOptions{path: filepath.Join(dir, "custom.sqlite3"), namespace: "different"}
	actual, err := withCodexDefaults(t.Context(), wanted, true, true)
	equal(t, must(t, actual, err), wanted)
}
