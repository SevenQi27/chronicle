package main

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"os"
	"regexp"
	"sort"
	"strings"
	"time"

	"modernc.org/sqlite"
)

type captureEvent struct {
	Origin    string `json:"origin"`
	TurnID    string `json:"turn_id"`
	Source    source `json:"source"`
	Workspace string `json:"workspace"`
	Private   bool   `json:"private"`
}

func (e *captureEvent) normalize() error {
	if err := trimFields(&e.Origin, &e.TurnID); err != nil {
		return err
	}
	return e.Source.normalize()
}

func textHash(text string) string {
	hash := sha256.Sum256([]byte(text))
	return hex.EncodeToString(hash[:])
}

func digest(value any) string {
	// Python used JSON with sorted keys and unescaped Unicode.
	canonical, err := rawValue[any](jsonString(value))
	if err != nil {
		panic(err)
	}
	return textHash(restoreUnicodeSeparators(jsonString(canonical)))
}

func restoreUnicodeSeparators(encoded string) string {
	if !strings.Contains(encoded, `\u202`) {
		return encoded
	}
	var result strings.Builder
	for i := 0; i < len(encoded); i++ {
		if encoded[i] == '\\' && i+1 < len(encoded) {
			switch {
			case strings.HasPrefix(encoded[i:], `\u2028`):
				result.WriteRune('\u2028')
				i += 5
			case strings.HasPrefix(encoded[i:], `\u2029`):
				result.WriteRune('\u2029')
				i += 5
			default:
				result.WriteByte(encoded[i])
				i++
				result.WriteByte(encoded[i])
			}
		} else {
			result.WriteByte(encoded[i])
		}
	}
	return result.String()
}

func defaultQueue(path, namespace string) string {
	return path + ".capture-" + digest(namespace)[:16] + ".sqlite3"
}

var privateSpan = regexp.MustCompile(`(?is)<private\b[^>]*>.*?(?:</private\s*>|$)`)
var credential = regexp.MustCompile(`\b(?:sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9]{16,}|AKIA[A-Z0-9]{16})\b`)
var credentialField = regexp.MustCompile(`(?im)((?:password|passwd|api[_-]?key|access[_-]?token|secret)\s*[=:]\s*)(?:"[^\s,;"']+"|'[^\s,;"']+'|[^\s,;"']+)`)
var optOut = regexp.MustCompile(`(?i)不要(?:保存|记录|记住)|do not (?:save|record|remember)|don.t (?:save|record|remember)`)

func redact(text string) string {
	text = privateSpan.ReplaceAllString(text, "[private]")
	text = credential.ReplaceAllString(text, "[credential]")
	return credentialField.ReplaceAllString(text, "${1}[credential]")
}

const queueSchema = `
CREATE TABLE IF NOT EXISTS events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_key TEXT NOT NULL UNIQUE, turn_key TEXT NOT NULL, origin TEXT NOT NULL,
    conversation_id TEXT NOT NULL, body TEXT, received_at TEXT NOT NULL, status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0, retry_at REAL NOT NULL DEFAULT 0,
    last_error TEXT, record_id TEXT, saved_at TEXT
);
CREATE INDEX IF NOT EXISTS capture_ready ON events(status,retry_at,sequence);
CREATE INDEX IF NOT EXISTS capture_turn ON events(turn_key,sequence);
`

type captureQueue struct {
	path string
	db   *sql.DB
}

func openQueue(ctx context.Context, path string) (*captureQueue, error) {
	if err := prepareFile(path); err != nil {
		return nil, err
	}
	db, err := sqliteDB(path, "busy_timeout(5000)", "secure_delete(ON)")
	if err != nil {
		return nil, err
	}
	if _, err = db.ExecContext(ctx, "PRAGMA journal_mode=WAL"); err == nil {
		_, err = db.ExecContext(ctx, queueSchema)
	}
	if err != nil {
		db.Close()
		return nil, err
	}
	if err := os.Chmod(path, 0600); err != nil {
		db.Close()
		return nil, err
	}
	return &captureQueue{path, db}, nil
}

func captureTurnKey(origin, conversation, turn string) string {
	return "capture-turn:" + digest([]string{origin, conversation, turn})
}

func (q *captureQueue) submit(ctx context.Context, event captureEvent, useReceiptTime bool) (object, error) {
	if err := event.normalize(); err != nil {
		return nil, err
	}
	key := digest([]string{event.Origin, event.Source.ID})
	turn := captureTurnKey(event.Origin, event.Source.ConversationID, event.TurnID)
	text := redact(event.Source.Content)
	private := event.Private || strings.TrimSpace(strings.ReplaceAll(text, "[private]", "")) == "" ||
		event.Source.Role == "user" && optOut.MatchString(event.Source.Content)
	event.Source.Content = text
	result := object{"accepted": true, "duplicate": false, "status": "queued", "event_key": key}
	err := transaction(ctx, q.db, "BEGIN IMMEDIATE", func(conn *sql.Conn) error {
		var forgotten, privateTurn bool
		if err := conn.QueryRowContext(ctx, `
SELECT EXISTS(SELECT 1 FROM events WHERE turn_key=? AND status='forgotten'),
       EXISTS(SELECT 1 FROM events WHERE turn_key=? AND status='private')`, turn, turn).Scan(&forgotten, &privateTurn); err != nil {
			return err
		}
		if forgotten {
			result = object{"accepted": false, "status": "forgotten", "event_key": key}
			return nil
		}
		var stored sql.NullString
		var status string
		err := conn.QueryRowContext(ctx, "SELECT body,status FROM events WHERE event_key=?", key).Scan(&stored, &status)
		if err == nil {
			if stored.Valid != !(private || privateTurn) {
				return invalid("capture event ID already exists with different content")
			}
			if stored.Valid {
				previous, err := rawValue[captureEvent](stored.String)
				if err != nil {
					return err
				}
				if useReceiptTime {
					event.Source.OccurredAt = previous.Source.OccurredAt
				}
				if jsonString(previous) != jsonString(event) {
					return invalid("capture event ID already exists with different content")
				}
			}
			result["duplicate"], result["status"] = true, status
			return nil
		}
		if !errors.Is(err, sql.ErrNoRows) {
			return err
		}
		var body any
		if private || privateTurn {
			status = "private"
		} else {
			status, body = "queued", jsonString(event)
		}
		_, err = conn.ExecContext(ctx, `
INSERT INTO events(event_key,turn_key,origin,conversation_id,body,received_at,status)
VALUES(?,?,?,?,?,?,?)`, key, turn, event.Origin, event.Source.ConversationID, body, stamp(nowUTC()), status)
		result["status"] = status
		return err
	})
	return result, err
}

type queuedEvent struct {
	key, turn string
	attempts  int
}

func (q *captureQueue) ready(ctx context.Context) ([]queuedEvent, error) {
	rows, err := q.db.QueryContext(ctx, `
SELECT event_key,turn_key,attempts FROM events WHERE status='queued' AND retry_at<=?
ORDER BY sequence LIMIT 20`, unixSeconds())
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var events []queuedEvent
	for rows.Next() {
		var event queuedEvent
		if err := rows.Scan(&event.key, &event.turn, &event.attempts); err != nil {
			return nil, err
		}
		events = append(events, event)
	}
	return events, rows.Err()
}

func unixSeconds() float64 { return float64(time.Now().UnixNano()) / float64(time.Second) }

func (q *captureQueue) turn(ctx context.Context, key string) ([]captureEvent, error) {
	rows, err := q.db.QueryContext(ctx, `
SELECT body FROM events WHERE turn_key=? AND body IS NOT NULL ORDER BY sequence`, key)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	var events []captureEvent
	for rows.Next() {
		var body string
		if err := rows.Scan(&body); err != nil {
			return nil, err
		}
		item, err := rawValue[captureEvent](body)
		if err != nil {
			return nil, err
		}
		events = append(events, item)
	}
	return events, rows.Err()
}

func (q *captureQueue) hasUser(ctx context.Context, session, turn string) (bool, error) {
	events, err := q.turn(ctx, captureTurnKey("codex", session, turn))
	for _, event := range events {
		if event.Source.Role == "user" {
			return true, nil
		}
	}
	return false, err
}

func (q *captureQueue) acknowledge(ctx context.Context, events []queuedEvent, recordID string, cause error) error {
	now := stamp(nowUTC())
	return transaction(ctx, q.db, "BEGIN IMMEDIATE", func(conn *sql.Conn) error {
		for _, event := range events {
			var err error
			if cause == nil {
				_, err = conn.ExecContext(ctx, `
UPDATE events SET status='saved',record_id=?,saved_at=?,last_error=NULL WHERE event_key=?`, recordID, now, event.key)
			} else {
				delay := min(60, 1<<min(event.attempts+1, 6))
				code := stringField(failure(cause)["error"].(map[string]any), "code")
				_, err = conn.ExecContext(ctx, `
UPDATE events SET attempts=attempts+1,last_error=?,retry_at=? WHERE event_key=?`,
					code+":"+fmt.Sprintf("%T", cause), unixSeconds()+float64(delay), event.key)
			}
			if err != nil {
				return err
			}
		}
		return nil
	})
}

func (q *captureQueue) status(ctx context.Context) (object, error) {
	result := object{}
	err := transaction(ctx, q.db, "BEGIN", func(conn *sql.Conn) error {
		rows, err := conn.QueryContext(ctx, "SELECT status,count(*) FROM events GROUP BY status")
		if err != nil {
			return err
		}
		counts := map[string]int{}
		for rows.Next() {
			var status string
			var count int
			if err := rows.Scan(&status, &count); err != nil {
				rows.Close()
				return err
			}
			counts[status] = count
		}
		if err := rows.Err(); err != nil {
			return err
		}
		var oldest sql.NullString
		if err := conn.QueryRowContext(ctx, "SELECT min(received_at) FROM events WHERE status='queued'").Scan(&oldest); err != nil {
			return err
		}
		rows, err = conn.QueryContext(ctx, `
SELECT origin,conversation_id,received_at,status,attempts,last_error,record_id,saved_at
FROM events ORDER BY sequence DESC LIMIT 10`)
		if err != nil {
			return err
		}
		defer rows.Close()
		recent := []object{}
		for rows.Next() {
			var origin, conversation, received, status string
			var attempts int
			var lastError, recordID, savedAt sql.NullString
			if err := rows.Scan(&origin, &conversation, &received, &status, &attempts, &lastError, &recordID, &savedAt); err != nil {
				return err
			}
			recent = append(recent, object{
				"origin": origin, "conversation_id": conversation, "received_at": received, "status": status, "attempts": attempts,
				"last_error": optionalString(lastError), "record_id": optionalString(recordID), "saved_at": optionalString(savedAt),
			})
		}
		result = object{"queue": q.path, "counts": counts, "pending": counts["queued"], "oldest_pending_at": optionalString(oldest), "recent": recent}
		return rows.Err()
	})
	return result, err
}

func optionalString(s sql.NullString) any {
	if !s.Valid {
		return nil
	}
	return s.String
}

var workerAlreadyRunning = invalid("A capture worker is already running for this queue")

func (q *captureQueue) withWorkerLock(ctx context.Context, body func() error) error {
	return withSQLiteWorkerLock(ctx, q.path+".worker-lock.sqlite3", body)
}

func withSQLiteWorkerLock(ctx context.Context, path string, body func() error) error {
	if err := prepareFile(path); err != nil {
		return err
	}
	lock, err := sqliteDB(path, "busy_timeout(0)")
	if err != nil {
		return err
	}
	defer lock.Close()
	conn, err := lock.Conn(ctx)
	if err != nil {
		return err
	}
	defer conn.Close()
	if _, err := conn.ExecContext(ctx, "BEGIN IMMEDIATE"); err != nil {
		var databaseError *sqlite.Error
		if errors.As(err, &databaseError) && option(databaseError.Code()&0xff, 5, 6) {
			return workerAlreadyRunning
		}
		return err
	}
	defer conn.ExecContext(context.Background(), "ROLLBACK")
	return body()
}

func (s *memoryStore) archiveTurn(ctx context.Context, turn string, events []captureEvent) (string, error) {
	if len(events) == 0 {
		return "", invalid("采集轮次没有原文")
	}
	sort.SliceStable(events, func(i, j int) bool {
		a, b := events[i].Source, events[j].Source
		if a.OccurredAt.Equal(b.OccurredAt.Time) {
			return a.ID < b.ID
		}
		return a.OccurredAt.Before(b.OccurredAt.Time)
	})
	first, last := events[0], events[len(events)-1]
	origin, conversation := first.Origin, first.Source.ConversationID
	conversationID := origin + ":" + conversation
	envelope := object{"capture_kind": "conversation_record", "origin": origin, "conversation_id": conversation,
		"turn_id": first.TurnID, "messages": events}
	snapshotHash := digest(envelope)
	sourceID := "capture-snapshot:" + snapshotHash
	evidence := make([]source, 0, len(events))
	references := make([]object, 0, len(events))
	ids := make([]string, 0, len(events))
	workspace, prompt := "", ""
	var hasUser, hasAnswer bool
	roles := map[string]string{"user": "用户", "assistant": "助手（未经用户确认）", "tool": "工具结果"}
	messages := make([]string, 0, len(events))
	for _, event := range events {
		if workspace == "" {
			workspace = event.Workspace
		}
		item := event.Source
		hasUser = hasUser || item.Role == "user"
		hasAnswer = hasAnswer || item.Role == "assistant"
		if prompt == "" && item.Role == "user" {
			prompt = item.Content
		}
		item.ID = "capture-source:" + digest([]string{event.Origin, item.ID})
		item.ConversationID = conversationID
		evidence = append(evidence, item)
		ids = append(ids, item.ID)
		references = append(references, object{
			"source_id": item.ID, "role": item.Role, "occurred_at": stamp(item.OccurredAt.Time), "content_sha256": textHash(item.Content),
		})
		messages = append(messages, roles[item.Role]+"：\n"+item.Content)
	}
	if prompt == "" {
		prompt = evidence[0].Content
	}
	envelope["format"], envelope["content_hash"], envelope["messages"] = "source-references-v1", snapshotHash, references
	manifest := source{sourceID, jsonString(envelope), "tool", last.Source.OccurredAt, conversationID}
	narrative := "自动采集的对话记录；不将助手回答视为已核实的用户事实。\n"
	contextFields := writeContext{Origin: &origin}
	entities := []entity{{ID: "capture-conversation:" + digest([]string{origin, conversation}),
		Name: origin + " 对话 " + limitRunes(conversation, 8), Category: "conversation"}}
	if workspace != "" {
		narrative += "工作目录：" + workspace + "\n"
		contextFields.Workspace = &workspace
		entities = append(entities, entity{ID: "capture-workspace:" + digest(workspace), Name: workspace, Category: "workspace"})
	}
	narrative += strings.Join(messages, "\n\n")
	precision := "range"
	if first.Source.OccurredAt.Equal(last.Source.OccurredAt.Time) {
		precision = "exact"
	}
	when := experienceTime{Start: &first.Source.OccurredAt, End: &last.Source.OccurredAt, Precision: precision}
	item := experience{ID: turn, Title: "对话 · " + limitRunes(strings.Join(strings.Fields(prompt), " "), 90),
		Narrative: narrative, When: when, Entities: entities, SourceIDs: ids}
	var recordID string
	err := transaction(ctx, s.db, "BEGIN IMMEDIATE", func(conn *sql.Conn) error {
		err := conn.QueryRowContext(ctx, `
SELECT record_id FROM chronicle_experience WHERE namespace=? AND logical_id=? AND source_id=?`,
			s.namespace, turn, sourceID).Scan(&recordID)
		if err == nil {
			return nil
		}
		if !errors.Is(err, sql.ErrNoRows) {
			return err
		}
		for _, original := range evidence {
			var raw string
			err := conn.QueryRowContext(ctx, "SELECT body FROM chronicle_source WHERE namespace=? AND id=?", s.namespace, original.ID).Scan(&raw)
			switch {
			case errors.Is(err, sql.ErrNoRows):
				err = s.insertSource(ctx, conn, original, jsonString(original), object{"status": "applied", "record_ids": []string{}})
			case err == nil:
				prior, parseErr := rawValue[source](raw)
				if parseErr != nil {
					return parseErr
				}
				if !prior.OccurredAt.Equal(original.OccurredAt.Time) || prior.ID != original.ID ||
					prior.Role != original.Role || prior.ConversationID != original.ConversationID || prior.Content != original.Content {
					return &apiError{"source_conflict", "采集来源 ID 已用于不同原文", object{"source_id": original.ID}}
				}
			}
			if err != nil {
				return err
			}
		}
		head, err := s.find(ctx, conn, turn)
		if err != nil {
			return err
		}
		if head.id != "" {
			item.Replaces = &head.id
		}
		result, err := s.writeIn(ctx, conn, writeRequest{Source: manifest, Experiences: []experience{item}, Context: &contextFields})
		if err != nil {
			return err
		}
		recordID = result["record_ids"].([]string)[0]
		if s.refineNewTurns && hasUser && hasAnswer {
			_, err := conn.ExecContext(ctx,
				"INSERT INTO chronicle_refinement(namespace, record_id) VALUES (?, ?) ON CONFLICT DO NOTHING",
				s.namespace, recordID)
			return err
		}
		return nil
	})
	return recordID, err
}

type captureProgress struct {
	Saved  int `json:"saved"`
	Failed int `json:"failed"`
}

func processPending(ctx context.Context, q *captureQueue, store *memoryStore, path, namespace string) (captureProgress, error) {
	var result captureProgress
	rows, err := q.ready(ctx)
	if err != nil {
		return result, err
	}
	groups := map[string][]queuedEvent{}
	var order []string
	for _, row := range rows {
		if _, found := groups[row.turn]; !found {
			order = append(order, row.turn)
		}
		groups[row.turn] = append(groups[row.turn], row)
	}
	for _, turn := range order {
		var id string
		if store == nil {
			store, err = openStore(ctx, path, namespace)
			if err == nil {
				defer store.db.Close()
			}
		}
		if store != nil {
			var events []captureEvent
			events, err = q.turn(ctx, turn)
			if err == nil {
				id, err = store.archiveTurn(ctx, turn, events)
			}
		}
		events := groups[turn]
		if ackErr := q.acknowledge(ctx, events, id, err); ackErr != nil {
			return result, ackErr
		}
		if err == nil {
			result.Saved += len(events)
		} else {
			result.Failed += len(events)
		}
	}
	return result, nil
}

func drainCapture(ctx context.Context, path string, store *memoryStore) error {
	if _, err := os.Stat(path); err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		return err
	}
	queue, err := openQueue(ctx, path)
	if err != nil {
		return err
	}
	defer queue.db.Close()
	err = queue.withWorkerLock(ctx, func() error {
		_, err := processPending(ctx, queue, store, store.path, store.namespace)
		return err
	})
	if errors.Is(err, workerAlreadyRunning) {
		return nil
	}
	return err
}

func runWorker(ctx context.Context, queue *captureQueue, path, namespace string, interval time.Duration, once bool, out io.Writer) error {
	if interval <= 0 {
		return invalid("capture interval must be positive")
	}
	return queue.withWorkerLock(ctx, func() error {
		timer := time.NewTicker(interval)
		defer timer.Stop()
		for {
			if ctx.Err() != nil {
				break
			}
			result, err := processPending(ctx, queue, nil, path, namespace)
			if err != nil && ctx.Err() == nil {
				return err
			}
			if once || result.Saved != 0 || result.Failed != 0 {
				if _, err := fmt.Fprintln(out, jsonString(result)); err != nil {
					return err
				}
			}
			if once {
				return nil
			}
			select {
			case <-ctx.Done():
			case <-timer.C:
			}
		}
		_, err := fmt.Fprintln(out, jsonString(object{"stopped": true}))
		return err
	})
}
