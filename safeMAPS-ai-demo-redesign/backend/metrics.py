"""
SafeMAPS — Runtime Metrics Collector

Provides a lightweight Prometheus-compatible /metrics endpoint.
No external dependencies — uses pure Python counters and atomics.

Tracks:
  route_requests_total        — total route compute calls
  route_requests_success      — successful route responses
  route_requests_error        — failed/no-path-found calls
  route_latency_p50_ms        — sliding 50th percentile latency
  route_latency_p95_ms        — sliding 95th percentile latency
  graph_cache_loaded          — 1 if loaded, 0 if not
  graph_cache_nodes_total     — total road nodes in memory
  graph_cache_edges_total     — total road edges in memory
  aqi_scrape_total            — lifetime AQI scrape cycles
  incident_scrape_total       — lifetime incident scrape cycles
  bidirectional_dispatches    — routes dispatched to BiDir A*
  standard_dispatches         — routes dispatched to standard A*
"""

import time
from collections import deque
from threading import Lock


class MetricsCollector:
    def __init__(self, window: int = 1000):
        self._lock = Lock()
        self._latencies: deque[float] = deque(maxlen=window)

        # Counters
        self.route_requests_total     = 0
        self.route_requests_success   = 0
        self.route_requests_error     = 0
        self.aqi_scrape_total         = 0
        self.incident_scrape_total    = 0
        self.bidirectional_dispatches = 0
        self.standard_dispatches      = 0

    # ── Recording methods ─────────────────────────────────────────────

    def record_route(self, success: bool, latency_ms: float,
                     bidirectional: bool = False) -> None:
        with self._lock:
            self.route_requests_total += 1
            if success:
                self.route_requests_success += 1
            else:
                self.route_requests_error += 1
            self._latencies.append(latency_ms)
            if bidirectional:
                self.bidirectional_dispatches += 1
            else:
                self.standard_dispatches += 1

    def record_aqi_scrape(self) -> None:
        with self._lock:
            self.aqi_scrape_total += 1

    def record_incident_scrape(self) -> None:
        with self._lock:
            self.incident_scrape_total += 1

    # ── Percentile helpers ────────────────────────────────────────────

    def _percentile(self, p: float) -> float:
        if not self._latencies:
            return 0.0
        sorted_lat = sorted(self._latencies)
        idx = max(0, int(len(sorted_lat) * p / 100) - 1)
        return sorted_lat[idx]

    # ── Prometheus text format ────────────────────────────────────────

    def to_prometheus(self) -> str:
        from graph_cache import graph_cache

        lines: list[str] = []

        def gauge(name: str, value: float, help_text: str = "") -> None:
            if help_text:
                lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} gauge")
            lines.append(f"{name} {value}")

        def counter(name: str, value: float, help_text: str = "") -> None:
            if help_text:
                lines.append(f"# HELP {name} {help_text}")
            lines.append(f"# TYPE {name} counter")
            lines.append(f"{name}_total {value}")

        with self._lock:
            counter("route_requests",  self.route_requests_total,
                    "Total route compute requests")
            counter("route_success",   self.route_requests_success,
                    "Successful route responses")
            counter("route_error",     self.route_requests_error,
                    "Failed or no-path-found route requests")
            counter("aqi_scrapes",     self.aqi_scrape_total,
                    "AQI scrape cycles completed")
            counter("incident_scrapes", self.incident_scrape_total,
                    "Incident scrape cycles completed")
            counter("bidir_dispatches", self.bidirectional_dispatches,
                    "Routes dispatched to bidirectional A*")
            counter("standard_dispatches", self.standard_dispatches,
                    "Routes dispatched to standard A*")

            gauge("route_latency_p50_ms", round(self._percentile(50), 2),
                  "50th percentile route latency (ms, last 1000 requests)")
            gauge("route_latency_p95_ms", round(self._percentile(95), 2),
                  "95th percentile route latency (ms, last 1000 requests)")

        # Graph cache stats
        is_loaded = 1 if graph_cache.is_loaded else 0
        gauge("graph_cache_loaded",       is_loaded,
              "1 if graph cache is loaded, 0 otherwise")
        gauge("graph_cache_nodes_total",  len(graph_cache.nodes),
              "Total road nodes in in-memory cache")
        gauge("graph_cache_edges_total",  len(graph_cache.edge_data),
              "Total road edges in in-memory cache")

        lines.append(f"# generated_at {time.time():.3f}")
        return "\n".join(lines) + "\n"


# ── Singleton ─────────────────────────────────────────────────────────
metrics = MetricsCollector()
