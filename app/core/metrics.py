"""Prometheus-compatible metrics for the investigation service.

Metrics are exposed as plain text in the OpenMetrics/Prometheus format
via the GET /api/v1/metrics endpoint. No external Prometheus client library
is required — this implementation uses a simple thread-safe counter store
so the service stays dependency-light.

Metric families:
  agio_investigation_events_total{status}       — event ingestion counts
  agio_investigation_investigations_total{status} — investigation lifecycle counts
  agio_investigation_evidence_records_total{result} — evidence collection results
  agio_investigation_findings_total{severity}   — rule engine findings
  agio_investigation_llm_requests_total{result} — LLM call outcomes
  agio_investigation_llm_duration_seconds       — LLM call duration histogram
  agio_investigation_processing_duration_seconds — event-to-investigation duration
  agio_investigation_dlq_messages_total         — messages sent to DLQ

Usage::

    from app.core.metrics import metrics

    metrics.inc("events", status="received")
    metrics.inc("events", status="processed")
    metrics.inc("events", status="duplicate")
    metrics.inc("events", status="failed")

    metrics.inc("investigations", status="created")
    metrics.inc("investigations", status="completed")
    metrics.inc("investigations", status="failed")

    metrics.observe_duration("llm", seconds=1.23)
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict
from typing import Any


class _Counter:
    """Thread-safe integer counter."""

    def __init__(self) -> None:
        self._value = 0
        self._lock = threading.Lock()

    def inc(self, by: int = 1) -> None:
        with self._lock:
            self._value += by

    @property
    def value(self) -> int:
        with self._lock:
            return self._value


class _Histogram:
    """Simple histogram with fixed buckets (sum + count + bucket counts)."""

    _BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, float("inf"))

    def __init__(self) -> None:
        self._sum = 0.0
        self._count = 0
        self._bucket_counts = [0] * len(self._BUCKETS)
        self._lock = threading.Lock()

    def observe(self, value: float) -> None:
        with self._lock:
            self._sum += value
            self._count += 1
            for i, bound in enumerate(self._BUCKETS):
                if value <= bound:
                    self._bucket_counts[i] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "sum": self._sum,
                "count": self._count,
                "buckets": list(zip(self._BUCKETS, self._bucket_counts)),
            }


class MetricsRegistry:
    """Central metrics store. Access via the module-level ``metrics`` singleton."""

    def __init__(self) -> None:
        self._counters: dict[str, _Counter] = defaultdict(_Counter)
        self._histograms: dict[str, _Histogram] = defaultdict(_Histogram)

    def inc(self, family: str, **labels: str) -> None:
        """Increment a labelled counter by 1."""
        key = self._counter_key(family, labels)
        self._counters[key].inc()

    def observe_duration(self, family: str, seconds: float) -> None:
        """Record a duration observation into a histogram."""
        self._histograms[family].observe(seconds)

    def get_counter(self, family: str, **labels: str) -> int:
        key = self._counter_key(family, labels)
        return self._counters[key].value

    def render_text(self) -> str:
        """Render all metrics as Prometheus text format."""
        lines: list[str] = []
        lines.append("# HELP agio_investigation_events_total Event ingestion counts by status")
        lines.append("# TYPE agio_investigation_events_total counter")
        for status in ("received", "processed", "duplicate", "failed", "dlq"):
            v = self.get_counter("events", status=status)
            lines.append(f'agio_investigation_events_total{{status="{status}"}} {v}')

        lines.append("# HELP agio_investigation_investigations_total Investigation lifecycle counts")
        lines.append("# TYPE agio_investigation_investigations_total counter")
        for status in ("created", "collecting", "analyzing", "completed", "failed", "review_required"):
            v = self.get_counter("investigations", status=status)
            lines.append(f'agio_investigation_investigations_total{{status="{status}"}} {v}')

        lines.append("# HELP agio_investigation_evidence_records_total Evidence collection results")
        lines.append("# TYPE agio_investigation_evidence_records_total counter")
        for result in ("stored", "missing", "api_error"):
            v = self.get_counter("evidence", result=result)
            lines.append(f'agio_investigation_evidence_records_total{{result="{result}"}} {v}')

        lines.append("# HELP agio_investigation_findings_total Rule engine findings by severity")
        lines.append("# TYPE agio_investigation_findings_total counter")
        for severity in ("informational", "low", "medium", "high", "critical"):
            v = self.get_counter("findings", severity=severity)
            lines.append(f'agio_investigation_findings_total{{severity="{severity}"}} {v}')

        lines.append("# HELP agio_investigation_llm_requests_total LLM call outcomes")
        lines.append("# TYPE agio_investigation_llm_requests_total counter")
        for result in ("success", "disabled", "failed", "timeout"):
            v = self.get_counter("llm_requests", result=result)
            lines.append(f'agio_investigation_llm_requests_total{{result="{result}"}} {v}')

        # LLM duration histogram
        hist = self._histograms["llm_duration"]
        snap = hist.snapshot()
        lines.append("# HELP agio_investigation_llm_duration_seconds LLM call duration")
        lines.append("# TYPE agio_investigation_llm_duration_seconds histogram")
        for bound, count in snap["buckets"]:
            le = "+Inf" if bound == float("inf") else str(bound)
            lines.append(f'agio_investigation_llm_duration_seconds_bucket{{le="{le}"}} {count}')
        lines.append(f'agio_investigation_llm_duration_seconds_sum {snap["sum"]}')
        lines.append(f'agio_investigation_llm_duration_seconds_count {snap["count"]}')

        # Processing duration histogram
        hist2 = self._histograms["processing_duration"]
        snap2 = hist2.snapshot()
        lines.append("# HELP agio_investigation_processing_duration_seconds Event-to-investigation duration")
        lines.append("# TYPE agio_investigation_processing_duration_seconds histogram")
        for bound, count in snap2["buckets"]:
            le = "+Inf" if bound == float("inf") else str(bound)
            lines.append(f'agio_investigation_processing_duration_seconds_bucket{{le="{le}"}} {count}')
        lines.append(f'agio_investigation_processing_duration_seconds_sum {snap2["sum"]}')
        lines.append(f'agio_investigation_processing_duration_seconds_count {snap2["count"]}')

        return "\n".join(lines) + "\n"

    @staticmethod
    def _counter_key(family: str, labels: dict[str, str]) -> str:
        label_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{family}{{{label_str}}}"


# Module-level singleton — import and use directly
metrics = MetricsRegistry()
