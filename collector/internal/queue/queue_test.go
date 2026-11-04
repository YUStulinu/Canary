package queue

import (
	"testing"
	"time"
)

func TestAllOrNothing(t *testing.T) {
	q := New[int](5)
	if !q.Push([]int{1, 2, 3}) {
		t.Fatal("first batch refused")
	}
	if q.Push([]int{4, 5, 6}) {
		t.Fatal("a batch that does not fit was taken")
	}
	if q.Len() != 3 {
		t.Fatalf("len %d", q.Len())
	}
	if got := q.Take(2, time.Millisecond); len(got) != 2 || got[0] != 1 {
		t.Fatalf("take %v", got)
	}
	q.Unshift([]int{9})
	if got := q.Take(10, time.Millisecond); len(got) != 2 || got[0] != 9 || got[1] != 3 {
		t.Fatalf("after unshift %v", got)
	}
}

func TestTakeWaitsThenTimesOut(t *testing.T) {
	q := New[int](5)
	go func() {
		time.Sleep(30 * time.Millisecond)
		q.Push([]int{7})
	}()
	if got := q.Take(5, time.Second); len(got) != 1 || got[0] != 7 {
		t.Fatalf("take %v", got)
	}
	start := time.Now()
	if got := q.Take(5, 50*time.Millisecond); len(got) != 0 {
		t.Fatalf("empty take %v", got)
	}
	if time.Since(start) < 40*time.Millisecond {
		t.Fatal("did not wait")
	}
	q.Close()
	if q.Push([]int{1}) {
		t.Fatal("closed queue took items")
	}
}
