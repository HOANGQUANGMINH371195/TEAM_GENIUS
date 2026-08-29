package main

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestProbeReportsStatusAndLatency(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusNoContent)
	}))
	defer server.Close()

	r := probe(context.Background(), server.Client(), server.URL, "/health")
	if !r.OK || r.Status != http.StatusNoContent || r.Endpoint != server.URL+"/health" {
		t.Fatalf("unexpected probe result: %+v", r)
	}
	if r.Latency < 0 {
		t.Fatalf("latency must be non-negative: %d", r.Latency)
	}
}

func TestProbeHonoursContextTimeout(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		time.Sleep(100 * time.Millisecond)
		w.WriteHeader(http.StatusOK)
	}))
	defer server.Close()

	ctx, cancel := timeWithTimeout(t, 5*time.Millisecond)
	defer cancel()
	r := probe(ctx, server.Client(), server.URL, "/ready")
	if r.OK || r.Error == "" {
		t.Fatalf("expected timeout result: %+v", r)
	}
}

func timeWithTimeout(t *testing.T, d time.Duration) (context.Context, context.CancelFunc) {
	t.Helper()
	return context.WithTimeout(context.Background(), d)
}
