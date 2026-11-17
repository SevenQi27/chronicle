package main

import (
	"context"
	"database/sql"
	"embed"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"runtime"
	"strconv"
	"syscall"
)

//go:embed static/graph/*
var graphFiles embed.FS

type graphRecord struct {
	id, logicalID, kind, recorded string
	body                          object
	evidence                      []string
}

func (s *memoryStore) graphSnapshot(ctx context.Context) (object, error) {
	now := stamp(nowUTC())
	records := []graphRecord{}
	sources := map[string]object{}
	err := transaction(ctx, s.db, "BEGIN", func(conn *sql.Conn) error {
		rows, err := conn.QueryContext(ctx, `
SELECT record_id,logical_id,record_kind,recorded_at,body,evidence_ids
FROM chronicle_experience AS r WHERE namespace=? AND recorded_at<=? AND NOT `+withdrawn+" ORDER BY seq DESC",
			s.namespace, now)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var row graphRecord
			var body, evidence string
			if err := rows.Scan(&row.id, &row.logicalID, &row.kind, &row.recorded, &body, &evidence); err != nil {
				return err
			}
			row.body, err = rawValue[object](body)
			if err != nil {
				return err
			}
			row.evidence, err = rawValue[[]string](evidence)
			if err != nil {
				return err
			}
			records = append(records, row)
		}
		if err := rows.Err(); err != nil {
			return err
		}
		sourcesRows, err := conn.QueryContext(ctx, "SELECT id,body FROM chronicle_source WHERE namespace=?", s.namespace)
		if err != nil {
			return err
		}
		defer sourcesRows.Close()
		for sourcesRows.Next() {
			var id, body string
			if err := sourcesRows.Scan(&id, &body); err != nil {
				return err
			}
			sources[id], err = rawValue[object](body)
			if err != nil {
				return err
			}
		}
		return sourcesRows.Err()
	})
	if err != nil {
		return nil, err
	}
	nodes := []object{}
	seen := map[string]bool{}
	latest := map[string]string{}
	addNode := func(id string, node object) {
		if !seen[id] {
			nodes = append(nodes, node)
			seen[id] = true
		}
	}
	for _, row := range records {
		if latest[row.logicalID] == "" {
			latest[row.logicalID] = row.id
		}
		addNode(row.id, object{"id": row.id, "record_id": row.id, "logical_id": row.logicalID, "kind": row.kind,
			"title": row.body["title"], "text": recordText(row.body), "recorded_at": row.recorded, "payload": row.body})
	}
	edges := []object{}
	addEdge := func(from, to, kind string) { edges = append(edges, object{"from": from, "to": to, "kind": kind}) }
	roles := map[string]string{"user": "用户原话", "tool": "工具结果", "assistant": "助手记录"}
	for _, row := range records {
		for _, id := range row.evidence {
			item, ok := sources[id]
			if !ok {
				continue
			}
			key := "source:" + id
			title := roles[stringField(item, "role")]
			if title == "" {
				title = "原始来源"
			}
			addNode(key, object{"id": key, "kind": "source", "payload": item, "text": item["content"], "title": title, "recorded_at": item["occurred_at"]})
			addEdge(row.id, key, "EVIDENCED_BY")
		}
		entities, _ := row.body["entities"].([]any)
		for _, raw := range entities {
			item, ok := raw.(map[string]any)
			if !ok {
				continue
			}
			key := "entity:" + stringField(item, "id")
			addNode(key, object{"id": key, "kind": "entity", "title": item["name"], "text": item["name"], "payload": item})
			addEdge(row.id, key, "INVOLVES")
		}
		references, _ := row.body["support_record_ids"].([]any)
		if len(references) == 0 {
			references, _ = row.body["supports"].([]any)
		}
		for _, reference := range references {
			target, _ := reference.(string)
			if !seen[target] {
				target = latest[target]
			}
			if seen[target] {
				addEdge(row.id, target, "SUPPORTED_BY")
			}
		}
		if previous := stringField(row.body, "replaces"); seen[previous] {
			kind := "REVISES"
			if row.body["revision_kind"] == "correct" {
				kind = "CORRECTS"
			}
			addEdge(row.id, previous, kind)
		}
	}
	return object{"namespace": s.namespace, "captured_at": now, "nodes": nodes, "edges": edges, "total_records": len(records),
		"limit": nil, "truncated": false}, nil
}

func graphHandler(store *memoryStore, port int) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		write := func(status int, mime string, content []byte) {
			h := w.Header()
			h.Set("Content-Type", mime+"; charset=utf-8")
			h.Set("Content-Length", strconv.Itoa(len(content)))
			h.Set("Cache-Control", "no-store")
			h.Set("X-Content-Type-Options", "nosniff")
			h.Set("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
			w.WriteHeader(status)
			_, _ = w.Write(content)
		}
		if r.Method != http.MethodGet {
			write(http.StatusNotImplemented, "text/plain", []byte("Unsupported method"))
			return
		}
		suffix := ":" + strconv.Itoa(port)
		if r.Host != "127.0.0.1"+suffix && r.Host != "localhost"+suffix {
			write(http.StatusForbidden, "text/plain", []byte("Forbidden"))
			return
		}
		if r.URL.EscapedPath() == "/api/graph" {
			result, err := store.graphSnapshot(r.Context())
			if err != nil {
				write(http.StatusServiceUnavailable, "application/json", []byte(jsonString(failure(err))))
				return
			}
			write(http.StatusOK, "application/json", []byte(jsonString(success(result))))
			return
		}
		var asset, mime string
		switch r.URL.EscapedPath() {
		case "/", "/graph":
			asset, mime = "index.html", "text/html"
		case "/graph/app.js":
			asset, mime = "app.js", "text/javascript"
		case "/graph/style.css":
			asset, mime = "style.css", "text/css"
		default:
			write(http.StatusNotFound, "text/plain", []byte("Not found"))
			return
		}
		content, err := graphFiles.ReadFile("static/graph/" + asset)
		if err != nil {
			write(http.StatusInternalServerError, "text/plain", []byte("Graph asset unavailable"))
			return
		}
		write(http.StatusOK, mime, content)
	})
}

func serveUI(ctx context.Context, path, namespace string, port int, out io.Writer) error {
	if port < 0 || port > 65535 {
		return invalid("--port 必须在 0–65535 之间")
	}
	store, err := openStore(ctx, path, namespace)
	if err != nil {
		return err
	}
	defer store.db.Close()
	listener, err := net.Listen("tcp4", fmt.Sprintf("127.0.0.1:%d", port))
	if err != nil {
		if errors.Is(err, syscall.EADDRINUSE) || runtime.GOOS == "windows" && errors.Is(err, syscall.Errno(10048)) {
			return &apiError{"port_in_use", fmt.Sprintf("端口 %d 已被占用。可以用 --port 指定其他端口。", port), nil}
		}
		return err
	}
	defer listener.Close()
	bound := listener.Addr().(*net.TCPAddr).Port
	server := &http.Server{Handler: graphHandler(store, bound)}
	if _, err := fmt.Fprintf(out, "Chronicle UI: http://127.0.0.1:%d/  (namespace: %s)\n", bound, namespace); err != nil {
		return err
	}
	done := make(chan struct{})
	defer close(done)
	go func() {
		select {
		case <-ctx.Done():
			_ = server.Close()
		case <-done:
		}
	}()
	err = server.Serve(listener)
	if errors.Is(err, http.ErrServerClosed) {
		return nil
	}
	return err
}
