# Performance Tests

Pytest-based performance tests that run against a real Empire server backed by a Dockerized MySQL instance. These tests validate that Empire handles concurrent load without connection pool exhaustion or event loop blocking.

## Background

These tests were built in response to 503/504 issues traced to two root causes:

1. **DB connection pool exhaustion** -- SQLAlchemy's default pool (size=5, overflow=10, 15 total) was overwhelmed by concurrent agent check-ins.

2. **Synchronous event loop blocking** -- CPU-heavy operations like C# stager compilation (`subprocess.run()`) ran directly on the FastAPI event loop, blocking all concurrent requests for the duration (~8 seconds per stager build).

### Why this approach

We evaluated several frameworks (Locust, k6, pytest-benchmark, wrk) and chose **custom pytest + httpx + asyncio** because:

- `httpx` was already a dev dependency -- zero new dependencies
- Tests live alongside the existing pytest suite with the same fixtures and markers
- Direct `assert` on latency percentiles and error rates
- Sufficient for proving specific bottlenecks (burst concurrency, not sustained load)

### Baseline results (before fixes)

| Scenario | Result |
|----------|--------|
| 50 concurrent requests | 90% error rate, p99 = 30s (pool timeout) |
| C# stager + concurrent mgmt API | Management API blocked 3.68s behind compilation |
| Server recovery after pool exhaustion | Never recovered -- required restart |

## Prerequisites

- Docker (for MySQL container)
- Poetry environment set up

## Running

```bash
DATABASE_USE=mysql poetry run pytest empire/test/test_performance/ -v --runslow
```

The `--runslow` flag is required -- performance tests are skipped by default to avoid slowing down the regular test suite.

To include the C# compiler blocking test (requires EmpireCompiler):

```bash
DATABASE_USE=mysql poetry run pytest empire/test/test_performance/ -v --runslow -m "slow or compiler"
```

## What Happens

1. A MySQL 8.0 Docker container starts on a random port
2. An Empire server subprocess starts against that MySQL instance
3. Tests fire concurrent HTTP requests and measure latency, error rates, and server health
4. Everything tears down automatically

## Test Structure

| File | What It Tests |
|------|---------------|
| `test_infra_smoke.py` | MySQL container, Empire server, auth, percentile math |
| `test_pool_exhaustion.py` | Concurrent DB-hitting requests don't exhaust the connection pool |
| `test_blocking.py` | Stager compilation doesn't block the management API |

### Pool exhaustion tests

Fire N concurrent requests against DB-hitting endpoints (`/api/v2/agents`, `/api/v2/listeners`, `/api/v2/users/me`) and assert:

- Zero connection errors
- Zero 5xx responses
- p99 latency under threshold
- No `QueuePool limit` messages in server logs

Parameterized at concurrency levels [10, 25, 50-xfail] to map the breaking point.

### Blocking tests

Fire a stager creation request (which triggers `subprocess.run()` for compilation) alongside concurrent management API probes. Assert that the probes complete within `MAX_MANAGEMENT_LATENCY_SECONDS` regardless of how long the stager takes. A C# compiler variant reproduces the exact production incident.

## Configurable Thresholds

Constants in `conftest.py` control test behavior:

| Constant | Default | Purpose |
|----------|---------|---------|
| `CONCURRENT_CHECKINS` | 25 | Number of simultaneous requests in pool test |
| `MAX_ERROR_RATE` | 0.0 | Maximum allowed error rate (0 = zero errors) |
| `MAX_P99_LATENCY_SECONDS` | 5.0 | p99 latency ceiling |
| `MAX_POOL_ERRORS` | 0 | Max `QueuePool limit` errors in server logs |
| `MAX_MANAGEMENT_LATENCY_SECONDS` | 1.0 | Max latency for management API during stager build |
| `STAGER_WAIT_BEFORE_PROBE_SECONDS` | 0.5 | Delay before probing management API |

## Pool Configuration

Empire's MySQL connection pool is configurable via the server config YAML:

```yaml
database:
  mysql:
    pool_size: 10        # base connections (default: 10)
    max_overflow: 15     # extra connections under load (default: 15)
    pool_pre_ping: true  # detect stale connections (default: true)
    pool_recycle: 3600   # recycle connections after N seconds (default: 3600)
```

The default pool (25 total connections) handles 25 concurrent requests. For heavier deployments, increase `pool_size` and `max_overflow`.
