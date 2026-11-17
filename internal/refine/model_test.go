package refine

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"reflect"
	"strings"
	"testing"
)

func fixtureInput() Input {
	return Input{
		Evidence: []Evidence{
			{SourceID: "user-1", RecordID: "experience-1", Role: "user", Content: "后端改用 Go。"},
			{SourceID: "assistant-1", RecordID: "experience-1", Role: "assistant", Content: "收到。"},
		},
		Current: []Understanding{
			{RecordID: "previous-1", Title: "后端语言", Statement: "后端采用 Python。"},
		},
	}
}

func reply(w http.ResponseWriter, finish, content string) {
	_ = json.NewEncoder(w).Encode(map[string]any{
		"choices": []any{map[string]any{
			"finish_reason": finish,
			"message":       chatMessage{Role: "assistant", Content: content},
		}},
	})
}

func newTestModel(t *testing.T, handler http.HandlerFunc) Model {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	model, err := NewChatCompletions(ChatConfig{BaseURL: server.URL, Model: "test"})
	if err != nil {
		t.Fatal(err)
	}
	return model
}

func TestChatCompletionsUsesSameContractWithOrWithoutAPIKey(t *testing.T) {
	for _, testCase := range []struct {
		name, basePath, apiKey string
	}{
		{name: "local", basePath: "/v1"},
		{name: "api key", apiKey: "test-key"},
	} {
		t.Run(testCase.name, func(t *testing.T) {
			input := fixtureInput()
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method != http.MethodPost || r.URL.Path != testCase.basePath+"/chat/completions" {
					t.Errorf("unexpected request: %s %s", r.Method, r.URL.Path)
				}
				var wantAuth string
				if testCase.apiKey != "" {
					wantAuth = "Bearer " + testCase.apiKey
				}
				if r.Header.Get("Authorization") != wantAuth {
					t.Error("unexpected authorization header")
				}
				var request chatRequest
				if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
					t.Error(err)
					w.WriteHeader(http.StatusBadRequest)
					return
				}
				if request.Model != "chosen-model" || request.ResponseFormat["type"] != "json_object" || request.MaxTokens != 500 {
					t.Error("unexpected completion parameters")
				}
				if len(request.Messages) != 2 || request.Messages[0].Role != "system" ||
					!strings.Contains(request.Messages[0].Content, "JSON") || request.Messages[1].Role != "user" {
					t.Error("unexpected extraction prompt")
					w.WriteHeader(http.StatusBadRequest)
					return
				}
				var sent Input
				if err := json.Unmarshal([]byte(request.Messages[1].Content), &sent); err != nil ||
					!reflect.DeepEqual(sent, input) {
					t.Errorf("archive evidence or current context was altered: %v", err)
				}
				reply(w, "stop", `{"candidates":[{"action":"update","target_record_id":"previous-1","title":"后端语言",
					"statement":"后端采用 Go。","basis":"explicit","source_ids":["user-1","assistant-1","user-1"]}]}`)
			}))
			defer server.Close()
			model, err := NewChatCompletions(ChatConfig{
				BaseURL: server.URL + testCase.basePath, Model: "chosen-model",
				APIKey: testCase.apiKey, MaxTokens: 500,
			})
			if err != nil {
				t.Fatal(err)
			}
			got, err := model.Extract(t.Context(), input)
			if err != nil {
				t.Fatal(err)
			}
			want := []Candidate{{
				Action: "update", TargetRecordID: "previous-1", Title: "后端语言", Statement: "后端采用 Go。",
				Basis: "explicit", SourceIDs: []string{"user-1", "assistant-1"}, SupportRecordIDs: []string{"experience-1"},
			}}
			if !reflect.DeepEqual(got, want) {
				t.Fatalf("got %#v, want %#v", got, want)
			}
		})
	}
}

func TestExtractionRejectsInvalidCandidates(t *testing.T) {
	for _, testCase := range []struct {
		name, content, want string
	}{
		{"missing", `{}`, "candidates array"},
		{"blank", "   ", "empty content"},
		{"trailing", `{"candidates":[]}{}`, "after the JSON object"},
		{"hallucinated source", `{"candidates":[{"action":"create","title":"例","statement":"例","basis":"inferred","source_ids":["missing"]}]}`, "source was not supplied"},
		{"hallucinated target", `{"candidates":[{"action":"update","target_record_id":"missing","title":"例","statement":"例","basis":"explicit","source_ids":["user-1"]}]}`, "target was not supplied"},
		{"assistant-only claim", `{"candidates":[{"action":"create","title":"例","statement":"例","basis":"explicit","source_ids":["assistant-1"]}]}`, "user source"},
	} {
		t.Run(testCase.name, func(t *testing.T) {
			model := newTestModel(t, func(w http.ResponseWriter, r *http.Request) {
				reply(w, "stop", testCase.content)
			})
			got, err := model.Extract(t.Context(), fixtureInput())
			if err == nil || !strings.Contains(err.Error(), testCase.want) || got != nil {
				t.Fatalf("got candidates=%#v, error=%v, want %q", got, err, testCase.want)
			}
		})
	}
}

func TestExtractionDistinguishesNoCandidateFromModelFailure(t *testing.T) {
	t.Run("empty candidates is success", func(t *testing.T) {
		model := newTestModel(t, func(w http.ResponseWriter, r *http.Request) {
			reply(w, "stop", `{"candidates":[]}`)
		})
		got, err := model.Extract(t.Context(), fixtureInput())
		if err != nil || got == nil || len(got) != 0 {
			t.Fatalf("got candidates=%#v, error=%v", got, err)
		}
	})
	t.Run("truncated output is failure", func(t *testing.T) {
		model := newTestModel(t, func(w http.ResponseWriter, r *http.Request) {
			reply(w, "length", `{"candidates":[]}`)
		})
		if _, err := model.Extract(t.Context(), fixtureInput()); err == nil {
			t.Fatal("accepted truncated completion")
		}
	})
	t.Run("provider failure exposes status only", func(t *testing.T) {
		model := newTestModel(t, func(w http.ResponseWriter, r *http.Request) {
			http.Error(w, "provider diagnostic", http.StatusTooManyRequests)
		})
		_, err := model.Extract(t.Context(), fixtureInput())
		var status *HTTPError
		if !errors.As(err, &status) || status.StatusCode != http.StatusTooManyRequests ||
			strings.Contains(err.Error(), "provider diagnostic") {
			t.Fatalf("unexpected provider error: %v", err)
		}
	})
	t.Run("request cancellation reaches the caller", func(t *testing.T) {
		model := newTestModel(t, func(w http.ResponseWriter, r *http.Request) {
			t.Error("canceled request reached server")
		})
		ctx, cancel := context.WithCancel(t.Context())
		cancel()
		if _, err := model.Extract(ctx, fixtureInput()); !errors.Is(err, context.Canceled) {
			t.Fatalf("expected cancellation, got %v", err)
		}
	})
}
