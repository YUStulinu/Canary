// Package ingest receives batches of log lines from agents and writes them to ClickHouse.
//
// POST /ingest takes newline-delimited JSON records. A batch is accepted whole (204) or refused
// whole: 400 if a line is malformed, 429 with Retry-After when the in-memory queue is full, which
// happens when ClickHouse cannot keep up or is down. Agents retry refused batches, so nothing is
// dropped. Writers take rows from the queue in large batches, the way ClickHouse likes them.
package ingest

import (
	"bufio"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"sync"
	"sync/atomic"
	"time"

	"github.com/YUStulinu/Canary/collector/internal/clickhouse"
	"github.com/YUStulinu/Canary/collector/internal/queue"
)

const maxBody = 64 << 20

// Store is where rows end up (ClickHouse, or a fake in tests).
type Store interface {
	Insert(rows []clickhouse.Row) error
}

type Stats struct {
	Received        atomic.Uint64
	RejectedBatches atomic.Uint64
	Written         atomic.Uint64
	WriteErrors     atomic.Uint64
	LastWriteMillis atomic.Int64
}

type Server struct {
	queue  *queue.Queue[clickhouse.Row]
	store  Store
	seq    atomic.Uint64
	Stats  Stats
	Batch  int
	Flush  time.Duration
	wg     sync.WaitGroup
	logger *log.Logger
}

func New(store Store, capacity int, lastSeq uint64, logger *log.Logger) *Server {
	s := &Server{queue: queue.New[clickhouse.Row](capacity), store: store, Batch: 50000, Flush: time.Second, logger: logger}
	s.seq.Store(lastSeq)
	return s
}

type record struct {
	Source    string   `json:"source"`
	Timestamp *float64 `json:"timestamp"`
	Level     string   `json:"level"`
	Component string   `json:"component"`
	Session   string   `json:"session"`
	Message   *string  `json:"message"`
	Label     string   `json:"label"`
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	switch {
	case r.URL.Path == "/ingest" && r.Method == http.MethodPost:
		s.ingest(w, r)
	case r.URL.Path == "/metrics":
		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(map[string]any{
			"received": s.Stats.Received.Load(), "written": s.Stats.Written.Load(),
			"rejected_batches": s.Stats.RejectedBatches.Load(), "write_errors": s.Stats.WriteErrors.Load(),
			"queued": s.queue.Len(), "last_write_ms": s.Stats.LastWriteMillis.Load(),
		})
	case r.URL.Path == "/healthz":
		w.Write([]byte("ok\n"))
	default:
		http.NotFound(w, r)
	}
}

func (s *Server) ingest(w http.ResponseWriter, r *http.Request) {
	sc := bufio.NewScanner(http.MaxBytesReader(w, r.Body, maxBody))
	sc.Buffer(make([]byte, 64*1024), 4<<20)
	var rows []clickhouse.Row
	line := 0
	for sc.Scan() {
		line++
		if len(sc.Bytes()) == 0 {
			continue
		}
		var rec record
		if err := json.Unmarshal(sc.Bytes(), &rec); err != nil || rec.Timestamp == nil || rec.Message == nil || rec.Source == "" {
			http.Error(w, fmt.Sprintf("line %d: needs source, timestamp and message", line), http.StatusBadRequest)
			return
		}
		rows = append(rows, clickhouse.Row{Timestamp: *rec.Timestamp, Source: rec.Source, Level: rec.Level,
			Component: rec.Component, Session: rec.Session, Message: *rec.Message, Label: rec.Label})
	}
	if err := sc.Err(); err != nil {
		http.Error(w, "reading the body: "+err.Error(), http.StatusBadRequest)
		return
	}
	// Sequence numbers in arrival order; numbers of a refused batch are simply skipped.
	last := s.seq.Add(uint64(len(rows)))
	for i := range rows {
		rows[i].Seq = last - uint64(len(rows)) + uint64(i) + 1
	}
	if !s.queue.Push(rows) {
		s.Stats.RejectedBatches.Add(1)
		w.Header().Set("Retry-After", "1")
		http.Error(w, "busy, retry", http.StatusTooManyRequests)
		return
	}
	s.Stats.Received.Add(uint64(len(rows)))
	w.WriteHeader(http.StatusNoContent)
}

// Start runs the writers.
func (s *Server) Start(writers int) {
	for i := 0; i < writers; i++ {
		s.wg.Add(1)
		go s.writer()
	}
}

func (s *Server) writer() {
	defer s.wg.Done()
	backoff := 100 * time.Millisecond
	for {
		rows := s.queue.Take(s.Batch, s.Flush)
		if len(rows) == 0 {
			if s.queue.Closed() && s.queue.Len() == 0 {
				return
			}
			continue
		}
		t0 := time.Now()
		if err := s.store.Insert(rows); err != nil {
			s.Stats.WriteErrors.Add(1)
			if s.logger != nil {
				s.logger.Printf("write of %d rows failed, retrying: %v", len(rows), err)
			}
			s.queue.Unshift(rows)
			time.Sleep(backoff)
			backoff = min(backoff*2, 10*time.Second)
			continue
		}
		backoff = 100 * time.Millisecond
		s.Stats.Written.Add(uint64(len(rows)))
		s.Stats.LastWriteMillis.Store(time.Since(t0).Milliseconds())
	}
}

// Shutdown stops taking batches and waits until everything queued is written.
func (s *Server) Shutdown() {
	s.queue.Close()
	s.wg.Wait()
}
