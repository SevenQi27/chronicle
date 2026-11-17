package main

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"strings"

	"github.com/SevenQi27/chronicle/internal/refine"
)

const refinementSchema = `
CREATE TABLE IF NOT EXISTS chronicle_refinement (
	namespace TEXT NOT NULL,
	record_id TEXT NOT NULL,
	done INTEGER NOT NULL DEFAULT 0,
	attempts INTEGER NOT NULL DEFAULT 0,
	retry_at REAL NOT NULL DEFAULT 0,
	last_error TEXT,
	PRIMARY KEY (namespace, record_id),
	FOREIGN KEY (record_id) REFERENCES chronicle_experience(record_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS chronicle_refinement_ready
	ON chronicle_refinement(namespace, done, retry_at);
`

func (s *memoryStore) enableRefinement(ctx context.Context) error {
	if _, err := s.db.ExecContext(ctx, refinementSchema); err != nil {
		return err
	}
	s.refineNewTurns = true
	return nil
}

type refinementTurn struct {
	manifest source
	context  writeContext
	evidence []refine.Evidence
}

func (s *memoryStore) loadRefinementTurn(ctx context.Context, conn *sql.Conn, id string) (refinementTurn, error) {
	var turn refinementTurn
	var raw string
	var origin, workspace sql.NullString
	err := conn.QueryRowContext(ctx, `
SELECT src.body, r.origin, r.workspace
FROM chronicle_experience AS r
JOIN chronicle_source AS src ON src.namespace=r.namespace AND src.id=r.source_id
WHERE r.namespace=? AND r.record_id=? AND r.record_kind='experience' AND NOT `+withdrawn+`
AND r.seq=(SELECT seq FROM chronicle_experience
           WHERE namespace=r.namespace AND logical_id=r.logical_id ORDER BY seq DESC LIMIT 1)`,
		s.namespace, id).Scan(&raw, &origin, &workspace)
	if err != nil {
		return turn, err
	}
	turn.manifest, err = rawValue[source](raw)
	if err != nil {
		return turn, err
	}
	if origin.Valid {
		turn.context.Origin = &origin.String
	}
	if workspace.Valid {
		turn.context.Workspace = &workspace.String
	}
	manifest, err := rawValue[struct {
		Messages []struct {
			SourceID string `json:"source_id"`
		} `json:"messages"`
	}](turn.manifest.Content)
	if err != nil {
		return turn, err
	}
	ids := make([]string, 0, len(manifest.Messages))
	for _, message := range manifest.Messages {
		ids = append(ids, message.SourceID)
	}
	originals, err := s.sources(ctx, conn, unique(ids))
	if err != nil {
		return turn, err
	}
	for _, original := range originals {
		turn.evidence = append(turn.evidence, refine.Evidence{
			SourceID: stringField(original, "id"),
			RecordID: id,
			Role:     stringField(original, "role"),
			Content:  stringField(original, "content"),
		})
	}
	return turn, nil
}

func (s *memoryStore) relevantUnderstandings(ctx context.Context, turn refinementTurn) ([]refine.Understanding, error) {
	var excerpts []string
	for _, item := range turn.evidence {
		if item.Role == "user" {
			excerpts = append(excerpts, item.Content)
		}
	}
	query := recallQuery{
		Query:      strings.Join(excerpts, "\n"),
		Limit:      8,
		recordKind: "understanding",
		Filters:    &memoryFilters{writeContext: writeContext{Workspace: turn.context.Workspace}},
	}
	recalled, err := s.recall(ctx, query)
	if err != nil {
		return nil, err
	}
	var current []refine.Understanding
	for _, hit := range recalled["results"].([]object) {
		item := refine.Understanding{RecordID: stringField(hit, "record_id")}
		err := s.db.QueryRowContext(ctx, `
SELECT json_extract(body, '$.title'), json_extract(body, '$.statement')
FROM chronicle_experience WHERE namespace=? AND record_id=?`, s.namespace, item.RecordID).
			Scan(&item.Title, &item.Statement)
		if errors.Is(err, sql.ErrNoRows) {
			continue
		}
		if err != nil {
			return nil, err
		}
		current = append(current, item)
	}
	return current, nil
}

func (s *memoryStore) finishRefinement(ctx context.Context, id string, turn refinementTurn, candidates []refine.Candidate) error {
	return transaction(ctx, s.db, "BEGIN IMMEDIATE", func(conn *sql.Conn) error {
		// Verify the evidence version is still current and visible after inference.
		var ready bool
		if err := conn.QueryRowContext(ctx, `
SELECT EXISTS (
	SELECT 1 FROM chronicle_experience AS r JOIN chronicle_refinement AS j
		ON j.namespace=r.namespace AND j.record_id=r.record_id
	WHERE r.namespace=? AND r.record_id=? AND j.done=0 AND NOT `+withdrawn+`
	AND r.seq=(SELECT seq FROM chronicle_experience
	           WHERE namespace=r.namespace AND logical_id=r.logical_id ORDER BY seq DESC LIMIT 1)
)`, s.namespace, id).Scan(&ready); err != nil {
			return err
		}
		if ready && len(candidates) > 0 {
			if err := s.saveRefinedCandidates(ctx, conn, id, turn, candidates); err != nil {
				return err
			}
		}
		_, err := conn.ExecContext(ctx, `
UPDATE chronicle_refinement SET done=1, last_error=NULL WHERE namespace=? AND record_id=?`, s.namespace, id)
		return err
	})
}

func (s *memoryStore) saveRefinedCandidates(ctx context.Context, conn *sql.Conn, id string, turn refinementTurn, candidates []refine.Candidate) error {
	request := writeRequest{
		Source: source{
			ID:             "refinement:" + digest(id),
			Role:           "tool",
			OccurredAt:     turn.manifest.OccurredAt,
			ConversationID: turn.manifest.ConversationID,
		},
		Context: &turn.context,
	}
	accepted := make([]refine.Candidate, 0, len(candidates))
	seen := make(map[string]bool, len(candidates))
	for _, candidate := range candidates {
		item := understanding{
			ID:        "refined-understanding:" + digest([]string{id, candidate.Title, candidate.Statement}),
			Title:     candidate.Title,
			Statement: candidate.Statement,
			Basis:     candidate.Basis,
			Supports:  []string{id},
		}
		if candidate.Action == "update" {
			target, err := s.find(ctx, conn, candidate.TargetRecordID)
			if err != nil {
				return err
			}
			if target.id != candidate.TargetRecordID || target.kind != "understanding" {
				return invalid("待更新的认识版本不存在")
			}
			item.ID, item.Replaces = target.logicalID, &target.id
		}
		key := digest([]string{item.ID, item.Title, item.Statement})
		if seen[key] {
			continue
		}
		seen[key] = true
		request.Understandings = append(request.Understandings, item)
		accepted = append(accepted, candidate)
	}
	request.Source.Content = jsonString(object{"capture_record_id": id, "candidates": accepted})
	_, err := s.writeIn(ctx, conn, request)
	return err
}

func (s *memoryStore) refineTurn(ctx context.Context, model refine.Model, id string) error {
	conn, err := s.db.Conn(ctx)
	if err != nil {
		return err
	}
	turn, err := s.loadRefinementTurn(ctx, conn, id)
	conn.Close()
	if errors.Is(err, sql.ErrNoRows) {
		return s.finishRefinement(ctx, id, refinementTurn{}, nil)
	}
	if err != nil {
		return err
	}
	current, err := s.relevantUnderstandings(ctx, turn)
	if err != nil {
		return err
	}
	candidates, err := model.Extract(ctx, refine.Input{Evidence: turn.evidence, Current: current})
	if err != nil {
		return err
	}
	return s.finishRefinement(ctx, id, turn, candidates)
}

func (s *memoryStore) drainRefinement(ctx context.Context, model refine.Model) error {
	lock := s.path + ".refine-" + digest(s.namespace)[:16] + ".worker-lock.sqlite3"
	err := withSQLiteWorkerLock(ctx, lock, func() error {
		var id string
		var attempts int
		err := s.db.QueryRowContext(ctx, `
SELECT record_id, attempts FROM chronicle_refinement
WHERE namespace=? AND done=0 AND retry_at<=? ORDER BY rowid LIMIT 1`,
			s.namespace, unixSeconds()).Scan(&id, &attempts)
		if errors.Is(err, sql.ErrNoRows) {
			return nil
		}
		if err != nil {
			return err
		}
		if err := s.refineTurn(ctx, model, id); err != nil {
			if ctx.Err() != nil {
				return ctx.Err()
			}
			code := "refinement_failed"
			var httpErr *refine.HTTPError
			if errors.As(err, &httpErr) {
				code = fmt.Sprintf("model_http_%d", httpErr.StatusCode)
			}
			delay := min(60, 1<<min(attempts+1, 6))
			if _, updateErr := s.db.ExecContext(ctx, `
UPDATE chronicle_refinement SET attempts=attempts+1, retry_at=?, last_error=?
WHERE namespace=? AND record_id=? AND done=0`,
				unixSeconds()+float64(delay), code, s.namespace, id); updateErr != nil {
				return updateErr
			}
			return err
		}
		return nil
	})
	if errors.Is(err, workerAlreadyRunning) {
		return nil
	}
	return err
}
