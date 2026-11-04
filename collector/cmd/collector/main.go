// Command collector receives log lines from Canary's agents and writes them to ClickHouse.
//
//	collector [-listen 127.0.0.1:8070] [-clickhouse http://127.0.0.1:8124] [-queue 1000000]
package main

import (
	"context"
	"flag"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/YUStulinu/Canary/collector/internal/clickhouse"
	"github.com/YUStulinu/Canary/collector/internal/ingest"
)

func main() {
	listen := flag.String("listen", "127.0.0.1:8070", "address to listen on")
	chURL := flag.String("clickhouse", env("CANARY_CLICKHOUSE", "http://127.0.0.1:8124"), "ClickHouse HTTP address")
	user := flag.String("user", env("CANARY_CLICKHOUSE_USER", "canary"), "ClickHouse user")
	password := flag.String("password", env("CANARY_CLICKHOUSE_PASSWORD", "canary"), "ClickHouse password")
	database := flag.String("database", "canary", "ClickHouse database")
	capacity := flag.Int("queue", 1_000_000, "rows held in memory before agents are asked to wait")
	batch := flag.Int("batch", 100_000, "rows per write")
	writers := flag.Int("writers", 2, "concurrent writers")
	flag.Parse()
	logger := log.New(os.Stderr, "collector: ", log.LstdFlags)

	ch := clickhouse.New(*chURL, *user, *password, *database)
	var last uint64
	for attempt := 0; ; attempt++ {
		var err error
		if last, err = ch.MaxSeq(); err == nil {
			break
		} else if attempt == 30 {
			logger.Fatalf("ClickHouse at %s: %v", *chURL, err)
		}
		time.Sleep(time.Second)
	}
	srv := ingest.New(ch, *capacity, last, logger)
	srv.Batch = *batch
	srv.Start(*writers)
	httpServer := &http.Server{Addr: *listen, Handler: srv, ReadHeaderTimeout: 10 * time.Second}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	go func() {
		<-ctx.Done()
		logger.Print("shutting down: writing what is queued")
		shut, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		httpServer.Shutdown(shut)
	}()
	logger.Printf("listening on %s, writing to %s (continuing after seq %d)", *listen, *chURL, last)
	if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
		logger.Fatal(err)
	}
	srv.Shutdown()
	logger.Printf("done: %d rows written", srv.Stats.Written.Load())
}

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}
