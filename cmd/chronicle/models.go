package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"math"
	"strings"
	"time"
)

type object = map[string]any

const utcLayout = "2006-01-02T15:04:05.000000+00:00"

func stamp(t time.Time) string {
	return t.UTC().Format(utcLayout)
}

type dateTime struct{ time.Time }

func (t dateTime) MarshalJSON() ([]byte, error) {
	layout := "2006-01-02T15:04:05Z07:00"
	if t.Nanosecond() != 0 {
		layout = "2006-01-02T15:04:05.000000Z07:00"
	}
	return json.Marshal(t.Format(layout))
}

func (t *dateTime) UnmarshalJSON(data []byte) error {
	var value string
	if err := json.Unmarshal(data, &value); err == nil && value != "" {
		parsed, err := time.Parse(time.RFC3339Nano, value)
		if err == nil {
			t.Time = parsed.Truncate(time.Microsecond)
			return nil
		}
	}
	var number float64
	if len(data) > 0 && (data[0] == '-' || data[0] >= '0' && data[0] <= '9') {
		if err := json.Unmarshal(data, &number); err == nil {
			if math.Abs(number) > 2e10 {
				number /= 1000
			}
			seconds, fraction := math.Modf(number)
			t.Time = time.Unix(int64(seconds), int64(math.Round(fraction*1e6))*1000).UTC()
			if t.Year() >= 1 && t.Year() <= 9999 {
				return nil
			}
		}
	}
	return invalid("时间必须带时区")
}

type source struct {
	ID             string   `json:"id"`
	Content        string   `json:"content"`
	Role           string   `json:"role"`
	OccurredAt     dateTime `json:"occurred_at"`
	ConversationID string   `json:"conversation_id"`
}

type experienceTime struct {
	Start       *dateTime `json:"start"`
	End         *dateTime `json:"end"`
	Precision   string    `json:"precision"`
	Description string    `json:"description"`
}

type entity struct {
	ID        string   `json:"id"`
	Name      string   `json:"name"`
	Category  string   `json:"category"`
	Aliases   []string `json:"aliases"`
	Operation string   `json:"operation"`
}

type experience struct {
	ID           string         `json:"id"`
	Title        string         `json:"title"`
	Narrative    string         `json:"narrative"`
	When         experienceTime `json:"when"`
	Entities     []entity       `json:"entities"`
	SourceIDs    []string       `json:"source_ids"`
	Replaces     *string        `json:"replaces"`
	RevisionKind string         `json:"revision_kind"`
}

type understanding struct {
	ID           string    `json:"id"`
	Title        string    `json:"title"`
	Statement    string    `json:"statement"`
	Basis        string    `json:"basis"`
	Supports     []string  `json:"supports"`
	ValidFrom    *dateTime `json:"valid_from"`
	ValidTo      *dateTime `json:"valid_to"`
	Replaces     *string   `json:"replaces"`
	RevisionKind string    `json:"revision_kind"`
}

type writeContext struct {
	Origin    *string `json:"origin"`
	Workspace *string `json:"workspace"`
}

type writeRequest struct {
	Source         source          `json:"source"`
	Experiences    []experience    `json:"experiences"`
	Context        *writeContext   `json:"context"`
	Understandings []understanding `json:"understandings,omitempty"`
}

type memoryFilters struct {
	writeContext
	ConversationID *string `json:"conversation_id"`
	SourceID       *string `json:"source_id"`
}

type recallQuery struct {
	recordKind      string
	Query           string         `json:"query"`
	Alternatives    []string       `json:"alternatives"`
	Filters         *memoryFilters `json:"filters"`
	Subject         string         `json:"subject"`
	At              *dateTime      `json:"at"`
	KnownAt         *dateTime      `json:"known_at"`
	Limit           int            `json:"limit"`
	IncludeInactive bool           `json:"include_inactive"`
	InterpretTime   bool           `json:"interpret_time"`
}

type detailQuery struct {
	EventIDs         []string  `json:"event_ids"`
	At               *dateTime `json:"at"`
	KnownAt          *dateTime `json:"known_at"`
	SourceMode       string    `json:"source_mode"`
	IncludeWithdrawn bool      `json:"include_withdrawn"`
}

type visibilityRequest struct {
	Action    string   `json:"action"`
	MemoryIDs []string `json:"memory_ids"`
	Reason    string   `json:"reason"`
}

type apiError struct {
	code string
	text string
	meta object
}

func (e *apiError) Error() string { return e.text }

func invalid(message string) error { return &apiError{"invalid_arguments", message, nil} }

func failure(err error) object {
	details := object{"code": "storage_error", "message": "无法读取或写入 SQLite 数据库"}
	var coded *apiError
	if errors.As(err, &coded) {
		details = object{"code": coded.code, "message": coded.text}
		for key, value := range coded.meta {
			details[key] = value
		}
	}
	return object{"ok": false, "error": details}
}

func success(value any) object { return object{"ok": true, "result": value} }

func decodeStrict(data []byte, destination any) error {
	data = bytes.TrimSpace(data)
	if len(data) == 0 || data[0] != '{' {
		return invalid("参数必须是一个 JSON 对象")
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(destination); err != nil {
		return invalid(err.Error())
	}
	if err := decoder.Decode(new(json.RawMessage)); err != io.EOF {
		return invalid("参数必须是一个 JSON 对象")
	}
	return nil
}

func option[T comparable](value T, options ...T) bool {
	for _, v := range options {
		if value == v {
			return true
		}
	}
	return false
}

func trimFields(fields ...*string) error {
	for _, field := range fields {
		*field = strings.TrimSpace(*field)
		if *field == "" {
			return invalid("文本字段不能为空")
		}
	}
	return nil
}

func trimOptional(fields ...*string) error {
	for _, field := range fields {
		if field != nil {
			if err := trimFields(field); err != nil {
				return err
			}
		}
	}
	return nil
}

func trimList(items []string) error {
	for i := range items {
		if err := trimFields(&items[i]); err != nil {
			return err
		}
	}
	return nil
}

func unique[T comparable](items []T) []T {
	result := make([]T, 0, len(items))
	seen := make(map[T]bool, len(items))
	for _, item := range items {
		if !seen[item] {
			result = append(result, item)
			seen[item] = true
		}
	}
	return result
}

func nonnull[T any](values []T) []T {
	if values == nil {
		return []T{}
	}
	return values
}

func (s *source) normalize() error {
	if s.Role == "" {
		s.Role = "user"
	}
	if s.ConversationID == "" {
		s.ConversationID = "default"
	}
	if err := trimFields(&s.ID, &s.Content, &s.ConversationID); err != nil {
		return err
	}
	if s.OccurredAt.IsZero() || !option(s.Role, "user", "tool", "assistant") {
		return invalid("来源需要有效的角色和带时区的消息时间")
	}
	return nil
}

func revision(kind *string, replaces *string) error {
	if *kind == "" {
		*kind = "update"
	}
	if !option(*kind, "update", "correct") {
		return invalid("revision_kind 必须是 update 或 correct")
	}
	return trimOptional(replaces)
}

func (e *experience) normalize() error {
	if err := trimFields(&e.ID, &e.Title, &e.Narrative); err != nil {
		return err
	}
	if err := revision(&e.RevisionKind, e.Replaces); err != nil {
		return err
	}
	e.SourceIDs = nonnull(e.SourceIDs)
	if err := trimList(e.SourceIDs); err != nil {
		return err
	}
	t := &e.When
	if t.Precision == "" {
		t.Precision = "unknown"
	}
	switch t.Precision {
	case "exact", "day", "range":
		if t.Start == nil {
			return invalid("明确时间精度需要 start")
		}
	case "unknown":
		if t.Start != nil || t.End != nil {
			return invalid("unknown 不应填造一个精确时间")
		}
	case "approximate":
	default:
		return invalid("无效的时间精度")
	}
	if t.End != nil && (t.Start == nil || t.End.Before(t.Start.Time)) {
		return invalid("经历结束时间必须不早于开始时间")
	}
	e.Entities = nonnull(e.Entities)
	if len(e.Entities) > 30 {
		return invalid("entities 最多包含 30 项")
	}
	for i := range e.Entities {
		item := &e.Entities[i]
		if item.Operation == "" {
			item.Operation = "mention"
		}
		if !option(item.Operation, "mention", "correct") {
			return invalid("无效的实体操作")
		}
		if item.Operation == "correct" && (item.Category == "" || item.Aliases == nil) {
			return invalid("实体 correct 必须明确提交 category 和完整 aliases")
		}
		if item.Category == "" {
			item.Category = "topic"
		}
		if err := trimFields(&item.ID, &item.Name, &item.Category); err != nil {
			return err
		}
		if len(item.Aliases) > 20 {
			return invalid("aliases 最多包含 20 项")
		}
		item.Aliases = nonnull(item.Aliases)
		if err := trimList(item.Aliases); err != nil {
			return err
		}
	}
	return nil
}

func (u *understanding) normalize() error {
	if err := trimFields(&u.ID, &u.Title, &u.Statement); err != nil {
		return err
	}
	if !option(u.Basis, "explicit", "inferred") || len(u.Supports) < 1 {
		return invalid("认识需要有效的 basis 和至少一条 supports")
	}
	if u.ValidFrom != nil && u.ValidTo != nil && !u.ValidTo.After(u.ValidFrom.Time) {
		return invalid("认识的 valid_to 必须晚于 valid_from")
	}
	if err := trimList(u.Supports); err != nil {
		return err
	}
	return revision(&u.RevisionKind, u.Replaces)
}

func (w *writeRequest) normalize() error {
	if err := w.Source.normalize(); err != nil {
		return err
	}
	if len(w.Experiences)+len(w.Understandings) == 0 {
		return invalid("至少填写一条经历或认识")
	}
	if w.Context != nil {
		if err := trimOptional(w.Context.Origin, w.Context.Workspace); err != nil {
			return err
		}
	}
	w.Experiences = nonnull(w.Experiences)
	for i := range w.Experiences {
		if err := w.Experiences[i].normalize(); err != nil {
			return err
		}
	}
	for i := range w.Understandings {
		if err := w.Understandings[i].normalize(); err != nil {
			return err
		}
	}
	return nil
}

func (q *recallQuery) normalize() error {
	if err := trimFields(&q.Query); err != nil {
		return err
	}
	if q.Limit < 1 || q.Limit > 50 || len(q.Alternatives) > 3 || q.InterpretTime {
		return invalid("limit 必须为 1–50，alternatives 最多 3 项，interpret_time 必须为 false")
	}
	if q.Filters != nil {
		if err := trimOptional(q.Filters.Origin, q.Filters.Workspace, q.Filters.ConversationID, q.Filters.SourceID); err != nil {
			return err
		}
	}
	return trimList(q.Alternatives)
}

func (q *detailQuery) normalize() error {
	if len(q.EventIDs) < 1 || len(q.EventIDs) > 50 || !option(q.SourceMode, "full", "summary") {
		return invalid("event_ids 必须包含 1–50 项，source_mode 必须为 full 或 summary")
	}
	return trimList(q.EventIDs)
}

func (q *visibilityRequest) normalize() error {
	if !option(q.Action, "withdraw", "restore") || len(q.MemoryIDs) == 0 {
		return invalid("需要 withdraw 或 restore 以及至少一个记忆 ID")
	}
	return trimList(q.MemoryIDs)
}

func viewTimes(at, known *dateTime) (string, string) {
	now := time.Now()
	effective, recorded := now, now
	if at != nil {
		effective = at.Time
	}
	if known != nil {
		recorded = known.Time
	}
	return stamp(effective), stamp(recorded)
}

func stringField(v object, key string) string {
	text, _ := v[key].(string)
	return text
}

func rawValue[T any](data string) (T, error) {
	var result T
	err := json.Unmarshal([]byte(data), &result)
	return result, err
}

func encode(value any) ([]byte, error) {
	var buffer bytes.Buffer
	encoder := json.NewEncoder(&buffer)
	encoder.SetEscapeHTML(false)
	if err := encoder.Encode(value); err != nil {
		return nil, err
	}
	return bytes.TrimSuffix(buffer.Bytes(), []byte("\n")), nil
}

func jsonString(value any) string {
	data, err := encode(value)
	if err != nil {
		panic(fmt.Sprintf("internal JSON value: %v", err))
	}
	return string(data)
}

func nowUTC() time.Time { return time.Now().UTC().Truncate(time.Microsecond) }
