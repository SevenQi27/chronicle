package main

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"io"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

var codexEvents = []string{"UserPromptSubmit", "Stop"}

type codexPayload struct {
	Event       string `json:"hook_event_name"`
	Session     string `json:"session_id"`
	Turn        string `json:"turn_id"`
	Transcript  string `json:"transcript_path"`
	Workspace   string `json:"cwd"`
	Prompt      string `json:"prompt"`
	LastMessage string `json:"last_assistant_message"`
}

func readLine(reader *bufio.Reader) ([]byte, error) {
	return reader.ReadBytes('\n')
}

func codexTranscript(path, session string) (object, error) {
	if path == "" {
		return nil, nil
	}
	file, err := os.Open(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	defer file.Close()
	line, err := readLine(bufio.NewReader(file))
	if err != nil && err != io.EOF {
		return nil, err
	}
	meta, err := rawValue[struct {
		Type    string `json:"type"`
		Payload object `json:"payload"`
	}](string(line))
	if err != nil || meta.Type != "session_meta" || stringField(meta.Payload, "thread_source") != "user" ||
		stringField(meta.Payload, "id") != session {
		return nil, nil
	}
	return meta.Payload, nil
}

func codexMessage(ctx context.Context, q *captureQueue, payload codexPayload, role, content string, at time.Time) (object, error) {
	content = strings.TrimSpace(strings.ReplaceAll(content, "\r\n", "\n"))
	if content == "" {
		return object{"skipped": "empty_message"}, nil
	}
	id := digest([]string{payload.Session, payload.Turn, role, content})
	return q.submit(ctx, captureEvent{Origin: "codex", TurnID: payload.Turn, Workspace: payload.Workspace,
		Source: source{id, content, role, dateTime{at}, payload.Session}}, true)
}

func activationPath(queue string) string {
	return strings.TrimSuffix(queue, filepath.Ext(queue)) + ".codex.json"
}

func recoverPrompt(ctx context.Context, q *captureQueue, payload codexPayload) (int, error) {
	data, err := os.ReadFile(activationPath(q.path))
	if errors.Is(err, os.ErrNotExist) {
		return 0, nil
	}
	if err != nil {
		return 0, err
	}
	activation, err := rawValue[struct {
		EnabledAt dateTime `json:"enabled_at"`
	}](string(data))
	if err != nil {
		return 0, err
	}
	file, err := os.Open(payload.Transcript)
	if err != nil {
		return 0, err
	}
	defer file.Close()
	reader := bufio.NewReader(file)
	found := 0
	for {
		line, err := readLine(reader)
		if err != nil && err != io.EOF {
			return found, err
		}
		if len(line) > 0 {
			added, err := recoverTranscriptLine(ctx, q, payload, activation.EnabledAt.Time, line)
			if err != nil {
				return found, err
			}
			if added {
				found++
			}
		}
		if err == io.EOF {
			return found, nil
		}
	}
}

func recoverTranscriptLine(ctx context.Context, q *captureQueue, payload codexPayload, cutoff time.Time, line []byte) (bool, error) {
	var entry struct {
		Type      string `json:"type"`
		Timestamp string `json:"timestamp"`
		Payload   struct {
			Type    string `json:"type"`
			Turn    string `json:"turn_id"`
			Session string `json:"thread_id"`
			Item    struct {
				Type    string   `json:"type"`
				Content []object `json:"content"`
			} `json:"item"`
		} `json:"payload"`
	}
	if json.Unmarshal(line, &entry) != nil || entry.Type != "event_msg" || entry.Payload.Type != "item_completed" ||
		entry.Payload.Turn != payload.Turn || entry.Payload.Session != payload.Session || entry.Payload.Item.Type != "UserMessage" {
		return false, nil
	}
	at, err := time.Parse(time.RFC3339Nano, entry.Timestamp)
	if err != nil {
		return false, err
	}
	if at.Before(cutoff) {
		return false, nil
	}
	var content []string
	for _, part := range entry.Payload.Item.Content {
		if strings.EqualFold(stringField(part, "type"), "text") {
			if text, ok := part["text"].(string); ok {
				content = append(content, text)
			}
		}
	}
	text := strings.Join(content, "\n")
	if strings.TrimSpace(text) == "" {
		return false, nil
	}
	_, err = codexMessage(ctx, q, payload, "user", text, at)
	return err == nil, err
}

func captureCodex(ctx context.Context, q *captureQueue, payload codexPayload) (object, error) {
	if !option(payload.Event, codexEvents...) {
		return object{"skipped": "unsupported_event"}, nil
	}
	if strings.TrimSpace(payload.Session) == "" || strings.TrimSpace(payload.Turn) == "" {
		return nil, invalid("Codex hook is missing session_id or turn_id")
	}
	meta, err := codexTranscript(payload.Transcript, payload.Session)
	if err != nil {
		return nil, err
	}
	if meta == nil {
		return object{"skipped": "not_a_verified_user_session"}, nil
	}
	role, content := "user", payload.Prompt
	if payload.Event == "Stop" {
		role, content = "assistant", payload.LastMessage
	}
	if strings.TrimSpace(content) == "" {
		return object{"skipped": "empty_message"}, nil
	}
	if payload.Workspace == "" {
		payload.Workspace = stringField(meta, "cwd")
	}
	recovered := 0
	if role == "assistant" {
		hasUser, err := q.hasUser(ctx, payload.Session, payload.Turn)
		if err != nil {
			return nil, err
		}
		if !hasUser {
			recovered, err = recoverPrompt(ctx, q, payload)
			if err != nil {
				return nil, err
			}
			hasUser, err = q.hasUser(ctx, payload.Session, payload.Turn)
			if err != nil {
				return nil, err
			}
			if !hasUser {
				return object{"skipped": "no_captured_user_prompt"}, nil
			}
		}
	}
	result, err := codexMessage(ctx, q, payload, role, content, nowUTC())
	if err == nil {
		result["recovered_prompts"] = recovered
	}
	return result, err
}

func codexHook(ctx context.Context, queuePath string, in io.Reader, out io.Writer) error {
	decision := object{"continue": true}
	if err := captureAndAudit(ctx, queuePath, in); err != nil {
		decision["systemMessage"] = "Chronicle capture was not queued. Check capture status."
	}
	return json.NewEncoder(out).Encode(decision)
}

func captureAndAudit(ctx context.Context, queuePath string, in io.Reader) error {
	var payload codexPayload
	if err := json.NewDecoder(in).Decode(&payload); err != nil {
		return err
	}
	q, err := openQueue(ctx, queuePath)
	if err != nil {
		return err
	}
	defer q.db.Close()
	result, err := captureCodex(ctx, q, payload)
	if err != nil {
		return err
	}
	log, err := os.OpenFile(filepath.Join(filepath.Dir(queuePath), "codex-hooks.jsonl"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0600)
	if err != nil {
		return err
	}
	defer log.Close()
	result["at"], result["event"], result["session_id"], result["turn_id"] = stamp(nowUTC()), payload.Event, payload.Session, payload.Turn
	return json.NewEncoder(log).Encode(result)
}

func unixQuote(value string) string {
	if value != "" && strings.IndexFunc(value, func(r rune) bool {
		return !(r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || strings.ContainsRune("_@%+=:,./-", r))
	}) < 0 {
		return value
	}
	return "'" + strings.ReplaceAll(value, "'", "'\"'\"'") + "'"
}

func hookCommand(executable, queue, platform string) string {
	if platform == "windows" {
		// Windows paths cannot contain double quotes. Quote both paths for spaces.
		return `"` + executable + `" capture hook-codex --queue "` + queue + `"`
	}
	return unixQuote(executable) + " capture hook-codex --queue " + unixQuote(queue)
}

func initialHookGroup(event string, hooks object) ([]any, error) {
	raw, exists := hooks[event]
	if !exists {
		return []any{}, nil
	}
	groups, ok := raw.([]any)
	if !ok {
		return nil, invalid("Invalid existing hooks.json")
	}
	return groups, nil
}

func matchOwnedHook(handler object, command, queue string) bool {
	value := stringField(handler, "command")
	if value == command {
		return true
	}
	for _, entry := range []string{" -m chronicle_memory.adapters.codex hook --queue ", " capture hook-codex --queue "} {
		_, target, ok := strings.Cut(value, entry)
		if ok && option(target, queue, unixQuote(queue), `"`+queue+`"`) {
			return true
		}
	}
	return false
}

func installCodexHooks(home, queue, executable string) (object, error) {
	path := filepath.Join(home, "hooks.json")
	config := object{}
	if data, err := os.ReadFile(path); err == nil {
		config, err = rawValue[object](string(data))
		if err != nil || config == nil {
			return nil, invalid("Invalid existing hooks.json")
		}
	} else if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	hooks := object{}
	if value, exists := config["hooks"]; exists {
		var ok bool
		hooks, ok = value.(map[string]any)
		if !ok {
			return nil, invalid("Invalid existing hooks.json")
		}
	}
	config["hooks"] = hooks
	command := hookCommand(executable, queue, runtime.GOOS)
	added := 0
	changed := false
	for _, event := range codexEvents {
		groups, err := initialHookGroup(event, hooks)
		if err != nil {
			return nil, err
		}
		matched := false
		for _, raw := range groups {
			group, ok := raw.(map[string]any)
			if !ok {
				return nil, invalid("Invalid existing hooks.json")
			}
			handlers, _ := group["hooks"].([]any)
			for _, raw := range handlers {
				handler, ok := raw.(map[string]any)
				if !ok || !matchOwnedHook(handler, command, queue) {
					continue
				}
				matched = true
				if handler["command"] != command || event == "Stop" && handler["async"] == true {
					handler["command"] = command
					if event == "Stop" {
						delete(handler, "async")
					}
					changed = true
				}
			}
		}
		if !matched {
			hooks[event] = append(groups, object{"hooks": []object{{"type": "command", "command": command, "timeout": 5,
				"statusMessage": "Queueing Chronicle conversation capture"}}})
			added++
		}
	}
	activation := activationPath(queue)
	if _, err := os.Stat(activation); added == len(codexEvents) || errors.Is(err, os.ErrNotExist) {
		if err := atomicWrite(activation, []byte(jsonString(object{"enabled_at": stamp(nowUTC())}))); err != nil {
			return nil, err
		}
	}
	if added > 0 || changed {
		data, err := json.MarshalIndent(config, "", "  ")
		if err != nil {
			return nil, err
		}
		if err := atomicWrite(path, append(data, '\n')); err != nil {
			return nil, err
		}
	}
	return object{"path": path, "command": command, "events": codexEvents, "requires_hook_trust": true}, nil
}

func atomicWrite(path string, data []byte) error {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	temp, err := os.CreateTemp(filepath.Dir(path), ".chronicle-*")
	if err != nil {
		return err
	}
	defer os.Remove(temp.Name())
	_, writeErr := temp.Write(data)
	closeErr := temp.Close()
	if writeErr != nil {
		return writeErr
	}
	if closeErr != nil {
		return closeErr
	}
	return os.Rename(temp.Name(), path)
}
