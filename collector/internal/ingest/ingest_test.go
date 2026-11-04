package ingest

import (
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/YUStulinu/Canary/collector/internal/clickhouse"
)

type fakeStore struct {
	mu   sync.Mutex
	rows []clickhouse.Row
	fail int // fail this many writes first
	down chan struct{}
}

func (f *fakeStore) Insert(rows []clickhouse.Row) error {
	if f.down != nil {
		<-f.down // a stalled database
	}
	f.mu.Lock()
	defer f.mu.Unlock()
	if f.fail > 0 {
		f.fail--
		return errors.New("unavailable")
	}
	f.rows = append(f.rows, rows...)
	return nil
}

func (f *fakeStore) count() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.rows)
}

func post(t *testing.T, s *Server, body string) *httptest.ResponseRecorder {
	t.Helper()
	w := httptest.NewRecorder()
	s.ServeHTTP(w, httptest.NewRequest(http.MethodPost, "/ingest", strings.NewReader(body)))
	return w
}

const line = `{"source":"hdfs","timestamp":1226262918.5,"level":"INFO","component":"c","session":"blk_1","message":"m","label":""}` + "\n"

func TestRowsAreWrittenInOrderAfterAFailure(t *testing.T) {
	store := &fakeStore{fail: 1}
	s := New(store, 100, 41, nil)
	s.Flush = 10 * time.Millisecond
	s.Start(1)
	if w := post(t, s, strings.Repeat(line, 3)); w.Code != http.StatusNoContent {
		t.Fatalf("status %d", w.Code)
	}
	s.Shutdown()
	if store.count() != 3 {
		t.Fatalf("wrote %d rows", store.count())
	}
	for i, r := range store.rows {
		if r.Seq != uint64(42+i) || r.Session != "blk_1" || r.Timestamp != 1226262918.5 {
			t.Errorf("row %d: %+v", i, r)
		}
	}
	if s.Stats.WriteErrors.Load() != 1 {
		t.Errorf("write errors %d", s.Stats.WriteErrors.Load())
	}
}

func TestMalformedBatchIsRefusedWhole(t *testing.T) {
	s := New(&fakeStore{}, 100, 0, nil)
	w := post(t, s, line+`{"source":"x","message":"no timestamp"}`+"\n")
	if w.Code != http.StatusBadRequest || !strings.Contains(w.Body.String(), "line 2") {
		t.Fatalf("status %d: %s", w.Code, w.Body.String())
	}
	if s.queue.Len() != 0 {
		t.Fatal("part of a bad batch was queued")
	}
}

func TestFullQueueAsksAgentsToWait(t *testing.T) {
	store := &fakeStore{down: make(chan struct{})}
	s := New(store, 5, 0, nil)
	s.Batch = 2
	s.Start(1)
	if w := post(t, s, strings.Repeat(line, 4)); w.Code != http.StatusNoContent {
		t.Fatalf("first batch: %d", w.Code)
	}
	time.Sleep(20 * time.Millisecond) // the writer takes 2 rows and stalls on the database
	w := post(t, s, strings.Repeat(line, 4))
	if w.Code != http.StatusTooManyRequests || w.Header().Get("Retry-After") == "" {
		t.Fatalf("second batch: %d", w.Code)
	}
	close(store.down)
	s.Shutdown()
	if store.count() != 4 || s.Stats.RejectedBatches.Load() != 1 {
		t.Fatalf("wrote %d, rejected %d", store.count(), s.Stats.RejectedBatches.Load())
	}
}
