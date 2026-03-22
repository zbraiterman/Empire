"""Shared utilities for performance tests."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx


@dataclass
class RequestResult:
    """Captures the outcome of a single HTTP request."""

    status_code: int = 0
    latency: float = 0.0
    error: str | None = None
    body: dict | None = field(default=None, repr=False)


def calculate_percentile(values: list[float], pct: float) -> float:
    """Return the *pct*-th percentile of *values* using linear interpolation.

    *pct* is expressed on the 0-100 scale (e.g. 99 for p99).
    """
    if not values:
        raise ValueError("cannot compute percentile of an empty sequence")
    sorted_vals = sorted(values)
    n = len(sorted_vals)
    if n == 1:
        return sorted_vals[0]
    # Map pct to a 0-based index in [0, n-1]
    k = (pct / 100) * (n - 1)
    lo = int(k)
    hi = lo + 1
    if hi >= n:
        return sorted_vals[-1]
    frac = k - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


async def timed_request(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    **kwargs,
) -> RequestResult:
    """Execute an HTTP request and return timing + result metadata."""
    start = time.perf_counter()
    try:
        resp = await client.request(method, url, **kwargs)
        elapsed = time.perf_counter() - start
        try:
            body = resp.json()
        except Exception:
            body = None
        return RequestResult(
            status_code=resp.status_code,
            latency=elapsed,
            body=body,
        )
    except Exception as exc:
        elapsed = time.perf_counter() - start
        return RequestResult(
            latency=elapsed,
            error=f"{type(exc).__name__}: {exc}",
        )


def summarize_results(results: list[RequestResult]) -> dict:
    """Produce a summary dict from a list of :class:`RequestResult`."""
    total = len(results)
    errors = [r for r in results if r.error is not None]
    latencies = [r.latency for r in results]

    status_codes: dict[int, int] = {}
    for r in results:
        status_codes[r.status_code] = status_codes.get(r.status_code, 0) + 1

    summary: dict = {
        "total": total,
        "errors": len(errors),
        "error_rate": len(errors) / total if total else 0.0,
        "status_codes": status_codes,
    }

    if latencies:
        summary["latency"] = {
            "min": min(latencies),
            "max": max(latencies),
            "mean": sum(latencies) / len(latencies),
            "p50": calculate_percentile(latencies, 50),
            "p95": calculate_percentile(latencies, 95),
            "p99": calculate_percentile(latencies, 99),
        }

    return summary
