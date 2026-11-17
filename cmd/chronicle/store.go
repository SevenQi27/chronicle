package main

import (
	"container/heap"
	"context"
	"database/sql"
	"errors"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"

	"github.com/google/uuid"
	_ "modernc.org/sqlite"
)

const schema = `
CREATE TABLE IF NOT EXISTS chronicle_source (
    namespace TEXT NOT NULL, id TEXT NOT NULL, body TEXT NOT NULL,
    request TEXT NOT NULL, result TEXT NOT NULL,
    PRIMARY KEY (namespace, id)
);
CREATE TABLE IF NOT EXISTS chronicle_experience (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, namespace TEXT NOT NULL,
    record_id TEXT NOT NULL UNIQUE, logical_id TEXT NOT NULL, source_id TEXT NOT NULL,
    recorded_at TEXT NOT NULL, effective_at TEXT, body TEXT NOT NULL, terms TEXT NOT NULL,
    origin TEXT, workspace TEXT, conversation_id TEXT NOT NULL, evidence_ids TEXT NOT NULL,
    record_kind TEXT NOT NULL DEFAULT 'experience', valid_to TEXT,
    FOREIGN KEY (namespace, source_id) REFERENCES chronicle_source(namespace, id)
);
CREATE INDEX IF NOT EXISTS chronicle_experience_head
    ON chronicle_experience(namespace, logical_id, seq DESC);
CREATE TABLE IF NOT EXISTS chronicle_withdrawal (
    namespace TEXT NOT NULL, logical_id TEXT NOT NULL, withdrawn_at TEXT NOT NULL,
    reason TEXT NOT NULL, PRIMARY KEY (namespace, logical_id)
);
`

const withdrawn = `EXISTS (
    SELECT 1 FROM chronicle_withdrawal AS w
    WHERE w.namespace=r.namespace AND w.logical_id=r.logical_id
)`

type memoryStore struct {
	path      string
	namespace string
	db        *sql.DB
}

func sqliteDB(path string, pragmas ...string) (*sql.DB, error) {
	name := filepath.ToSlash(path)
	if filepath.VolumeName(path) != "" && !strings.HasPrefix(name, "/") {
		name = "/" + name
	}
	uri := url.URL{Path: name}
	options := url.Values{"_pragma": pragmas}
	return sql.Open("sqlite", "file:"+uri.EscapedPath()+"?"+options.Encode())
}

func prepareFile(path string) error {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	file, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if errors.Is(err, os.ErrExist) {
		return nil
	}
	if err != nil {
		return err
	}
	return file.Close()
}

func transaction(ctx context.Context, db *sql.DB, mode string, body func(*sql.Conn) error) error {
	conn, err := db.Conn(ctx)
	if err != nil {
		return err
	}
	defer conn.Close()
	if _, err = conn.ExecContext(ctx, mode); err != nil {
		return err
	}
	defer conn.ExecContext(context.Background(), "ROLLBACK")
	if err = body(conn); err != nil {
		return err
	}
	_, err = conn.ExecContext(ctx, "COMMIT")
	return err
}

func openStore(ctx context.Context, path, namespace string) (*memoryStore, error) {
	if strings.TrimSpace(namespace) == "" {
		return nil, invalid("namespace 不能为空")
	}
	if err := prepareFile(path); err != nil {
		return nil, err
	}
	db, err := sqliteDB(path, "busy_timeout(5000)", "foreign_keys(ON)")
	if err != nil {
		return nil, err
	}
	store := &memoryStore{path, namespace, db}
	err = transaction(ctx, db, "BEGIN IMMEDIATE", func(conn *sql.Conn) error {
		if _, err := conn.ExecContext(ctx, schema); err != nil {
			return err
		}
		return migrateStore(ctx, conn)
	})
	if err != nil {
		db.Close()
		return nil, err
	}
	return store, nil
}

func migrateStore(ctx context.Context, conn *sql.Conn) error {
	rows, err := conn.QueryContext(ctx, "SELECT name FROM pragma_table_info('chronicle_experience')")
	if err != nil {
		return err
	}
	columns := map[string]bool{}
	for rows.Next() {
		var name string
		if err := rows.Scan(&name); err != nil {
			rows.Close()
			return err
		}
		columns[name] = true
	}
	if err := rows.Err(); err != nil {
		return err
	}
	if !columns["record_kind"] {
		if _, err := conn.ExecContext(ctx, "ALTER TABLE chronicle_experience ADD COLUMN record_kind TEXT NOT NULL DEFAULT 'experience'"); err != nil {
			return err
		}
	}
	if !columns["valid_to"] {
		if _, err := conn.ExecContext(ctx, "ALTER TABLE chronicle_experience ADD COLUMN valid_to TEXT"); err != nil {
			return err
		}
		if err := backfillExpiry(ctx, conn); err != nil {
			return err
		}
	}
	var exists int
	if err := conn.QueryRowContext(ctx, "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name='chronicle_experience_fts')").Scan(&exists); err != nil {
		return err
	}
	if exists == 1 {
		return nil
	}
	_, err = conn.ExecContext(ctx, `
CREATE VIRTUAL TABLE chronicle_experience_fts USING fts5(
    terms, content='', detail=none, tokenize='unicode61 tokenchars _'
);
INSERT INTO chronicle_experience_fts(rowid, terms)
SELECT seq, (SELECT group_concat(value, ' ') FROM json_each(r.terms))
FROM chronicle_experience AS r;
CREATE TRIGGER chronicle_experience_fts_insert AFTER INSERT ON chronicle_experience
BEGIN
    INSERT INTO chronicle_experience_fts(rowid, terms)
    VALUES (new.seq, (SELECT group_concat(value, ' ') FROM json_each(new.terms)));
END;
`)
	return err
}

func backfillExpiry(ctx context.Context, conn *sql.Conn) error {
	rows, err := conn.QueryContext(ctx, `
SELECT seq, json_extract(body, '$.valid_to') FROM chronicle_experience
WHERE record_kind='understanding' AND json_extract(body, '$.valid_to') IS NOT NULL`)
	if err != nil {
		return err
	}
	type expiry struct {
		seq int64
		at  dateTime
	}
	var dates []expiry
	for rows.Next() {
		var seq int64
		var value string
		if err := rows.Scan(&seq, &value); err != nil {
			rows.Close()
			return err
		}
		parsed, err := rawValue[dateTime](jsonString(value))
		if err != nil {
			rows.Close()
			return err
		}
		dates = append(dates, expiry{seq, parsed})
	}
	if err := rows.Err(); err != nil {
		return err
	}
	for _, item := range dates {
		if _, err := conn.ExecContext(ctx, "UPDATE chronicle_experience SET valid_to=? WHERE seq=?", stamp(item.at.Time), item.seq); err != nil {
			return err
		}
	}
	return nil
}

type recordHead struct {
	id, logicalID, kind, evidence string
}

func (s *memoryStore) head(ctx context.Context, conn *sql.Conn, logicalID string) (recordHead, error) {
	var result recordHead
	err := conn.QueryRowContext(ctx, `
SELECT record_id, logical_id, record_kind, evidence_ids FROM chronicle_experience
WHERE namespace=? AND logical_id=? ORDER BY seq DESC LIMIT 1`, s.namespace, logicalID).
		Scan(&result.id, &result.logicalID, &result.kind, &result.evidence)
	if errors.Is(err, sql.ErrNoRows) {
		err = nil
	}
	return result, err
}

func (s *memoryStore) find(ctx context.Context, conn *sql.Conn, reference string) (recordHead, error) {
	var result recordHead
	err := conn.QueryRowContext(ctx, `
SELECT record_id, logical_id, record_kind, evidence_ids FROM chronicle_experience
WHERE namespace=? AND record_id=?`, s.namespace, reference).
		Scan(&result.id, &result.logicalID, &result.kind, &result.evidence)
	if errors.Is(err, sql.ErrNoRows) {
		return s.head(ctx, conn, reference)
	}
	return result, err
}

func (s *memoryStore) insertSource(ctx context.Context, conn *sql.Conn, item source, payload string, result object) error {
	_, err := conn.ExecContext(ctx, "INSERT INTO chronicle_source VALUES (?, ?, ?, ?, ?)",
		s.namespace, item.ID, jsonString(item), payload, jsonString(result))
	return err
}

func (s *memoryStore) sources(ctx context.Context, conn *sql.Conn, identifiers []string) ([]object, error) {
	rows, err := conn.QueryContext(ctx, `
SELECT id, body FROM chronicle_source WHERE namespace=? AND id IN (SELECT value FROM json_each(?))`,
		s.namespace, jsonString(identifiers))
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	byID := make(map[string]object, len(identifiers))
	for rows.Next() {
		var id, body string
		if err := rows.Scan(&id, &body); err != nil {
			return nil, err
		}
		item, err := rawValue[object](body)
		if err != nil {
			return nil, err
		}
		byID[id] = item
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	result := make([]object, 0, len(identifiers))
	for _, id := range identifiers {
		item, ok := byID[id]
		if !ok {
			return nil, invalid("记忆引用了当前分区中不存在的来源")
		}
		result = append(result, item)
	}
	return result, nil
}

func (s *memoryStore) write(ctx context.Context, request writeRequest) (object, error) {
	var result object
	err := transaction(ctx, s.db, "BEGIN IMMEDIATE", func(conn *sql.Conn) error {
		var err error
		result, err = s.writeIn(ctx, conn, request)
		return err
	})
	return result, err
}

func (s *memoryStore) writeIn(ctx context.Context, conn *sql.Conn, request writeRequest) (object, error) {
	if err := request.normalize(); err != nil {
		return nil, err
	}
	if request.Source.Role == "assistant" {
		if len(request.Experiences) > 0 {
			return nil, invalid("助手复述不能作为新经历或明确表达的来源")
		}
		for _, item := range request.Understandings {
			if item.Basis == "explicit" {
				return nil, invalid("助手复述不能作为新经历或明确表达的来源")
			}
		}
	}
	payload := jsonString(request)
	var oldPayload, oldResult string
	err := conn.QueryRowContext(ctx, `
SELECT request, result FROM chronicle_source WHERE namespace=? AND id=?`,
		s.namespace, request.Source.ID).Scan(&oldPayload, &oldResult)
	switch {
	case err == nil:
		// Older releases serialized JSON with their own field order and escaping.
		old, oldErr := rawValue[object](oldPayload)
		current, currentErr := rawValue[object](payload)
		if oldErr != nil || currentErr != nil || jsonString(old) != jsonString(current) {
			return nil, &apiError{"source_conflict", "相同来源 ID 已用于不同写入", object{"source_id": request.Source.ID}}
		}
		return rawValue[object](oldResult)
	case !errors.Is(err, sql.ErrNoRows):
		return nil, err
	}
	ids := make([]string, len(request.Experiences)+len(request.Understandings))
	for i := range ids {
		ids[i] = uuid.NewString()
	}
	recorded := stamp(nowUTC())
	result := object{
		"source_id": request.Source.ID, "event_ids": ids, "record_ids": ids, "association_ids": []string{},
		"review_ids": []string{}, "status": "applied", "recorded_at": recorded,
	}
	if err := s.insertSource(ctx, conn, request.Source, payload, result); err != nil {
		return nil, err
	}
	context := writeContext{}
	if request.Context != nil {
		context = *request.Context
	}
	origin := request.Source
	for i, item := range request.Experiences {
		terms := []string{item.Title, item.Narrative}
		for _, entity := range item.Entities {
			terms = append(terms, entity.ID, entity.Name)
			terms = append(terms, entity.Aliases...)
		}
		fields := writeRecord{ids[i], item.ID, "experience", jsonString(item), item.Replaces,
			item.When.Start, nil, terms, item.SourceIDs}
		if err := s.insertRecord(ctx, conn, origin, context, recorded, fields); err != nil {
			return nil, err
		}
	}
	for i, item := range request.Understandings {
		supports := make([]string, 0, len(item.Supports))
		for _, reference := range unique(item.Supports) {
			support, err := s.find(ctx, conn, reference)
			if err != nil {
				return nil, err
			}
			if support.kind != "experience" {
				return nil, invalid("supports 必须引用当前分区中已有的经历：" + reference)
			}
			supports = append(supports, support.id)
		}
		body := struct {
			understanding
			SupportRecordIDs []string `json:"support_record_ids"`
		}{item, unique(supports)}
		fields := writeRecord{ids[len(request.Experiences)+i], item.ID, "understanding", jsonString(body),
			item.Replaces, item.ValidFrom, item.ValidTo, []string{item.Title, item.Statement}, nil}
		if err := s.insertRecord(ctx, conn, origin, context, recorded, fields); err != nil {
			return nil, err
		}
	}
	return result, nil
}

type writeRecord struct {
	id, logicalID, kind, body string
	replaces                  *string
	effective, validTo        *dateTime
	searchText, sourceIDs     []string
}

func optionalStamp(value *dateTime) any {
	if value == nil {
		return nil
	}
	return stamp(value.Time)
}

func (s *memoryStore) insertRecord(ctx context.Context, conn *sql.Conn, origin source, context writeContext, recorded string, fields writeRecord) error {
	head, err := s.head(ctx, conn, fields.logicalID)
	if err != nil {
		return err
	}
	evidence := append([]string{origin.ID}, fields.sourceIDs...)
	if head.id != "" {
		if head.kind != fields.kind {
			return invalid("同一记忆 ID 不能改变类型")
		}
		if fields.replaces == nil || *fields.replaces != head.id {
			return &apiError{"version_conflict", "请读取最新版本再更新这条记忆",
				object{"current_record_id": head.id, "memory_id": fields.logicalID}}
		}
		previous, err := rawValue[[]string](head.evidence)
		if err != nil {
			return err
		}
		evidence = append(evidence, previous...)
	} else if fields.replaces != nil {
		return invalid("replaces 与此记忆 ID 不匹配")
	}
	evidence = unique(evidence)
	if _, err := s.sources(ctx, conn, evidence); err != nil {
		return err
	}
	_, err = conn.ExecContext(ctx, `
INSERT INTO chronicle_experience(namespace,record_id,logical_id,source_id,recorded_at,effective_at,
body,terms,origin,workspace,conversation_id,evidence_ids,record_kind,valid_to)
VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)`, s.namespace, fields.id, fields.logicalID, origin.ID, recorded,
		optionalStamp(fields.effective), fields.body, jsonString(tokens(strings.Join(fields.searchText, " "))),
		context.Origin, context.Workspace, origin.ConversationID, jsonString(evidence), fields.kind, optionalStamp(fields.validTo))
	return err
}

func (s *memoryStore) visibility(ctx context.Context, request visibilityRequest) (object, error) {
	keys := make([]string, 0, len(request.MemoryIDs))
	hide := request.Action == "withdraw"
	err := transaction(ctx, s.db, "BEGIN IMMEDIATE", func(conn *sql.Conn) error {
		for _, reference := range request.MemoryIDs {
			record, err := s.find(ctx, conn, reference)
			if err != nil {
				return err
			}
			if record.id == "" {
				return invalid("当前分区找不到记忆：" + reference)
			}
			keys = append(keys, record.logicalID)
		}
		keys = unique(keys)
		for _, key := range keys {
			var err error
			if hide {
				_, err = conn.ExecContext(ctx, `INSERT INTO chronicle_withdrawal(namespace,logical_id,withdrawn_at,reason)
VALUES(?,?,?,?) ON CONFLICT(namespace,logical_id) DO NOTHING`, s.namespace, key, stamp(nowUTC()), request.Reason)
			} else {
				_, err = conn.ExecContext(ctx, "DELETE FROM chronicle_withdrawal WHERE namespace=? AND logical_id=?", s.namespace, key)
			}
			if err != nil {
				return err
			}
		}
		return nil
	})
	return object{"action": request.Action, "memory_ids": keys, "withdrawn": hide}, err
}

var latinWords = regexp.MustCompile(`[a-z0-9_]+`)
var chineseWords = regexp.MustCompile(`[\x{3400}-\x{9fff}]+`)

func tokens(text string) []string {
	terms := latinWords.FindAllString(strings.ToLower(text), -1)
	for _, part := range chineseWords.FindAllString(text, -1) {
		runes := []rune(part)
		if len(runes) == 1 {
			terms = append(terms, part)
		}
		for i := 1; i < len(runes); i++ {
			terms = append(terms, string(runes[i-1:i+1]))
		}
	}
	return unique(terms)
}

func limitRunes(text string, limit int) string {
	for index := range text {
		if limit == 0 {
			return text[:index]
		}
		limit--
	}
	return text
}

type candidate struct {
	score int
	seq   int64
	value object
}

type candidateHeap []candidate

func (h candidateHeap) Len() int { return len(h) }
func (h candidateHeap) Less(i, j int) bool {
	return h[i].score < h[j].score || h[i].score == h[j].score && h[i].seq < h[j].seq
}
func (h candidateHeap) Swap(i, j int) { h[i], h[j] = h[j], h[i] }
func (h *candidateHeap) Push(x any)   { *h = append(*h, x.(candidate)) }
func (h *candidateHeap) Pop() any {
	values := *h
	value := values[len(values)-1]
	values[len(values)-1] = candidate{}
	*h = values[:len(values)-1]
	return value
}

func (s *memoryStore) recall(ctx context.Context, q recallQuery) (object, error) {
	at, known := viewTimes(q.At, q.KnownAt)
	phrases := unique(append([]string{q.Query}, q.Alternatives...))
	phraseTerms := make(map[string][]string, len(phrases))
	var all []string
	for _, phrase := range phrases {
		phraseTerms[phrase] = tokens(phrase)
		all = append(all, phraseTerms[phrase]...)
	}
	all = unique(all)
	results := []object{}
	answer := object{"status": "ok", "at": at, "known_at": known, "results": results}
	if len(all) == 0 {
		return answer, nil
	}
	sort.Strings(all)
	for i, term := range all {
		all[i] = `"` + term + `"`
	}
	query := `SELECT r.seq,r.record_id,r.source_id,r.recorded_at,r.body,r.terms,r.record_kind
FROM chronicle_experience_fts CROSS JOIN chronicle_experience AS r
WHERE chronicle_experience_fts MATCH ? AND r.seq=chronicle_experience_fts.rowid
AND r.namespace=? AND r.recorded_at<=? AND NOT ` + withdrawn + `
AND (r.effective_at IS NULL OR r.effective_at<=?) AND (r.valid_to IS NULL OR r.valid_to>?)
AND r.seq=(SELECT newer.seq FROM chronicle_experience AS newer
           WHERE newer.namespace=r.namespace AND newer.logical_id=r.logical_id AND newer.recorded_at<=?
           ORDER BY newer.seq DESC LIMIT 1)`
	params := []any{strings.Join(all, " OR "), s.namespace, known, at, at, known}
	if f := q.Filters; f != nil {
		for _, filter := range []struct {
			name  string
			value *string
		}{
			{"origin", f.Origin}, {"workspace", f.Workspace}, {"conversation_id", f.ConversationID},
		} {
			if filter.value != nil {
				query += " AND r." + filter.name + "=?"
				params = append(params, *filter.value)
			}
		}
		if f.SourceID != nil {
			query += " AND EXISTS (SELECT 1 FROM json_each(r.evidence_ids) WHERE value=?)"
			params = append(params, *f.SourceID)
		}
	}
	rows, err := s.db.QueryContext(ctx, query, params...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	best := candidateHeap{}
	for rows.Next() {
		var seq int64
		var id, sourceID, recorded, body, terms, kind string
		if err := rows.Scan(&seq, &id, &sourceID, &recorded, &body, &terms, &kind); err != nil {
			return nil, err
		}
		words, err := rawValue[[]string](terms)
		if err != nil {
			return nil, err
		}
		wordSet := make(map[string]bool, len(words))
		for _, word := range words {
			wordSet[word] = true
		}
		var score int
		matched := []string{}
		for _, phrase := range phrases {
			hits := 0
			for _, word := range phraseTerms[phrase] {
				if wordSet[word] {
					hits++
				}
			}
			if hits > 0 {
				matched = append(matched, phrase)
				score = max(score, hits)
			}
		}
		if score == 0 || len(best) == q.Limit && (score < best[0].score || score == best[0].score && seq < best[0].seq) {
			continue
		}
		item, err := rawValue[object](body)
		if err != nil {
			return nil, err
		}
		if q.Subject != "" && !matchesSubject(q.Subject, item) {
			continue
		}
		value := object{
			"record_id": id, "record_kind": kind, "id": item["id"], "title": item["title"], "summary": limitRunes(recordText(item), 320),
			"status": "active", "when": item["when"], "recorded_at": recorded, "source_id": sourceID, "score": score,
			"match_scope": "current", "matched_record_ids": []string{id}, "matched_queries": matched,
		}
		if kind == "understanding" {
			value["basis"] = item["basis"]
		}
		if len(best) == q.Limit {
			heap.Pop(&best)
		}
		heap.Push(&best, candidate{score, seq, value})
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	results = make([]object, len(best))
	for i := len(results) - 1; i >= 0; i-- {
		results[i] = heap.Pop(&best).(candidate).value
	}
	answer["results"] = results
	return answer, nil
}

func recordText(item object) string {
	if text, ok := item["narrative"].(string); ok {
		return text
	}
	return stringField(item, "statement")
}

func matchesSubject(subject string, body object) bool {
	if subject == stringField(body, "id") || subject == stringField(body, "title") {
		return true
	}
	entities, _ := body["entities"].([]any)
	for _, raw := range entities {
		item, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		if subject == stringField(item, "id") || subject == stringField(item, "name") {
			return true
		}
		aliases, _ := item["aliases"].([]any)
		for _, alias := range aliases {
			if text, ok := alias.(string); ok && text == subject {
				return true
			}
		}
	}
	return false
}

func (s *memoryStore) details(ctx context.Context, q detailQuery) ([]object, error) {
	at, known := viewTimes(q.At, q.KnownAt)
	result := []object{}
	err := transaction(ctx, s.db, "BEGIN", func(conn *sql.Conn) error {
		for _, id := range unique(q.EventIDs) {
			var logicalID, kind, sourceID, recorded, body, evidence string
			var effective sql.NullString
			var hidden bool
			err := conn.QueryRowContext(ctx, `
SELECT r.logical_id,r.record_kind,r.source_id,r.recorded_at,r.effective_at,r.body,r.evidence_ids,`+withdrawn+`
FROM chronicle_experience AS r WHERE r.namespace=? AND r.record_id=? AND r.recorded_at<=?`, s.namespace, id, known).
				Scan(&logicalID, &kind, &sourceID, &recorded, &effective, &body, &evidence, &hidden)
			if errors.Is(err, sql.ErrNoRows) {
				continue
			}
			if err != nil {
				return err
			}
			if hidden && !q.IncludeWithdrawn {
				continue
			}
			var head string
			if err := conn.QueryRowContext(ctx, `
SELECT record_id FROM chronicle_experience WHERE namespace=? AND logical_id=? AND recorded_at<=?
ORDER BY seq DESC LIMIT 1`, s.namespace, logicalID, known).Scan(&head); err != nil {
				return err
			}
			ids, err := rawValue[[]string](evidence)
			if err != nil {
				return err
			}
			sources, err := s.sources(ctx, conn, ids)
			if err != nil {
				return err
			}
			if len(sources) == 0 {
				return invalid("记忆缺少来源")
			}
			if q.SourceMode == "summary" {
				for _, item := range sources {
					item["content_chars"] = len([]rune(stringField(item, "content")))
					item["content_omitted"] = true
					delete(item, "content")
				}
			}
			item, err := rawValue[object](body)
			if err != nil {
				return err
			}
			item["event_id"], item["record_id"], item["record_kind"] = id, id, kind
			item["source_id"], item["source"], item["sources"] = sourceID, sources[0], sources
			item["withdrawn"], item["source_count"], item["source_mode"] = hidden, len(sources), q.SourceMode
			item["recorded_at"], item["at"], item["known_at"] = recorded, at, known
			item["superseded"] = head != id || effective.Valid && effective.String > at
			result = append(result, item)
		}
		return nil
	})
	return result, err
}
