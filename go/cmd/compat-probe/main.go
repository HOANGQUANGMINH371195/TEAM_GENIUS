// compat-probe is a read-only Phase 1 harness.  It deliberately has no
// database or provider SDK dependencies: the first Go milestone measures the
// transport/health overhead without changing production traffic.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"
)

type result struct {
	Endpoint string `json:"endpoint"`
	Status   int    `json:"status"`
	OK       bool   `json:"ok"`
	Latency  int64  `json:"latency_ms"`
	Error    string `json:"error,omitempty"`
}

func probe(ctx context.Context, client *http.Client, base, path string) result {
	endpoint := strings.TrimRight(base, "/") + path
	started := time.Now()
	r := result{Endpoint: endpoint}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		r.Error = err.Error()
		return r
	}
	resp, err := client.Do(req)
	r.Latency = time.Since(started).Milliseconds()
	if err != nil {
		r.Error = err.Error()
		return r
	}
	defer resp.Body.Close()
	r.Status = resp.StatusCode
	r.OK = resp.StatusCode >= 200 && resp.StatusCode < 300
	if !r.OK {
		r.Error = resp.Status
	}
	return r
}

func main() {
	base := flag.String("url", getenv("P151_API_URL", "http://localhost:8000"), "API base URL")
	timeout := flag.Duration("timeout", getenvDuration("P151_PROBE_TIMEOUT", 5*time.Second), "per-probe timeout")
	flag.Parse()

	client := &http.Client{Timeout: *timeout}
	paths := []string{"/health", "/ready"}
	results := make([]result, len(paths))
	var wg sync.WaitGroup
	for i, path := range paths {
		wg.Add(1)
		go func(i int, path string) {
			defer wg.Done()
			ctx, cancel := context.WithTimeout(context.Background(), *timeout)
			defer cancel()
			results[i] = probe(ctx, client, *base, path)
		}(i, path)
	}
	wg.Wait()

	enc := json.NewEncoder(os.Stdout)
	enc.SetIndent("", "  ")
	if err := enc.Encode(results); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
	for _, r := range results {
		if !r.OK {
			os.Exit(2)
		}
	}
}

func getenv(key, fallback string) string {
	if value := os.Getenv(key); value != "" {
		return value
	}
	return fallback
}

func getenvDuration(key string, fallback time.Duration) time.Duration {
	value := os.Getenv(key)
	if value == "" {
		return fallback
	}
	d, err := time.ParseDuration(value)
	if err != nil || d <= 0 {
		return fallback
	}
	return d
}
