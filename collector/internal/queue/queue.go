// Package queue holds received log rows in memory until they are written, with a fixed capacity:
// a batch goes in whole or not at all, so a full queue turns into backpressure on the agents
// instead of lost lines.
package queue

import (
	"sync"
	"time"
)

type Queue[T any] struct {
	mu       sync.Mutex
	nonEmpty *sync.Cond
	items    []T
	capacity int
	closed   bool
}

func New[T any](capacity int) *Queue[T] {
	q := &Queue[T]{capacity: capacity}
	q.nonEmpty = sync.NewCond(&q.mu)
	return q
}

// Push adds all items, or none if they do not fit; it reports which.
func (q *Queue[T]) Push(items []T) bool {
	q.mu.Lock()
	defer q.mu.Unlock()
	if q.closed || len(q.items)+len(items) > q.capacity {
		return false
	}
	q.items = append(q.items, items...)
	q.nonEmpty.Broadcast()
	return true
}

// Take waits until there are items (or the wait passes, or the queue closes) and removes up to max.
// It returns nil once the queue is closed and empty.
func (q *Queue[T]) Take(max int, wait time.Duration) []T {
	q.mu.Lock()
	defer q.mu.Unlock()
	if len(q.items) == 0 && !q.closed {
		timer := time.AfterFunc(wait, func() {
			q.mu.Lock()
			q.nonEmpty.Broadcast()
			q.mu.Unlock()
		})
		q.nonEmpty.Wait()
		timer.Stop()
	}
	n := min(max, len(q.items))
	out := make([]T, n)
	copy(out, q.items[:n])
	q.items = q.items[n:]
	if len(q.items) == 0 {
		q.items = nil // let the backing array go
	}
	return out
}

// Unshift puts items back at the front (a write that failed is retried first).
func (q *Queue[T]) Unshift(items []T) {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.items = append(append(make([]T, 0, len(items)+len(q.items)), items...), q.items...)
	q.nonEmpty.Broadcast()
}

func (q *Queue[T]) Len() int {
	q.mu.Lock()
	defer q.mu.Unlock()
	return len(q.items)
}

func (q *Queue[T]) Close() {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.closed = true
	q.nonEmpty.Broadcast()
}

func (q *Queue[T]) Closed() bool {
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.closed
}
