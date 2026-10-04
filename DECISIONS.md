# Architectural & Design Decisions: FieldNet Monitor

## 1. Modeling State and Time
### The Three Clocks & Virtual Time Engine
Real-world IoT telemetry suffers from clock skew, arbitrary network latency, and out-of-order deliveries. We modeled time across three distinct dimensions:
1. **Device Event Time (`sent_at`, `finished_at`, `taken_at`, `acked_at`)**: Set by device hardware. Checked against server arrival time to enforce the rule that device timestamps $\le$ received time + 5 minutes.
2. **Server Clock (`received_at`, `recorded_at`)**: Tracks the moment payloads arrive at the ingestion boundary.
3. **Virtual Clock Abstraction (`monitor/clock.py`)**: All business logic and models read time exclusively through `get_now()`. In production (`TEST_MODE=0`), this defaults to UTC `datetime.now()`. In testing (`TEST_MODE=1`), it reads from a singleton `SystemState.virtual_clock` database entity. Advancing the virtual clock via `/test/clock` executes queued work and evaluates state machines chronologically in event-time order without skipping due events.

### State Axes & Strict One-Way Dependencies
State axes are isolated but strictly coupled in a unidirectional dependency chain:
$$\text{Gateway Status / Command State} \longrightarrow \text{Gateway Coverage Class} \longrightarrow \text{Sensor Coverage} \longrightarrow \text{Sensor Collection \& Lifecycle}$$
A sensor's state never mutates a gateway's state. All entity updates append an immutable `TimelineEntry` (`seq`, `kind`, `axis`, `from`, `to`, `effective_at`, `recorded_at`, `rule`, `evidence_ids`). Past timeline entries are never edited or deleted.

---

## 2. Where Each Rule Lives
* **`monitor/models.py`**: Declares schemas for `Gateway`, `Sensor`, `Batch`, `Reading`, `CycleReport`, `CycleResult`, `Command`, `TimelineEntry`, and `SystemState`. Computes derived properties (`coverage_class`, `flags`).
* **`monitor/engine.py`**: Core domain logic and state transitions:
  - `compute_gateway_coverage_class()` & `compute_sensor_coverage()`: Evaluates multi-gateway best-class precedence (`available > stopped > recoverable > none`).
  - `compute_sensor_collection()`: Implements 24-hour outcome precedence (`readings > no_readings > could_not_read > timed_out`).
  - `evaluate_gateway_transitions()`: Enforces the 12-hour stale rule and 10-minute command timeouts.
  - `classify_sensor_day()`: Evaluates complete UTC days into `reading`, `unresolved`, `quiet`, or `unchecked`.
  - `evaluate_sensor()`: Drives active, dormant (14 available days), and sampling (72h window) state machines.
  - `recompute_sensor_lifecycle()`: Handles late backlogged readings via historical replay.
  - `process_batch()`: Ingestion validation, unit checks, duplicate filtering, quarantine, and retries.
* **`monitor/views_gw.py`**: Fixed Gateway API (`/gw/v1/...`) with Bearer token authentication, 401/403 status codes, and request idempotency.
* **`monitor/views_api.py`**: Operator console endpoints (`/api/v1/...`) with input validation and action handlers.
* **`monitor/views_test.py`**: Deterministic test endpoints (`/test/reset`, `/test/clock`, `/test/drain`, `/test/faults`).

---

## 3. Retries, Late Data & Corrections
### Background Ingestion & Retry Backoff
Batches are ingested asynchronously or processed deterministically via the test drain. If processing fails (or injected test faults are active), the batch moves to `retrying` with exponential backoff $2^{k-1}$ minutes on the server clock. After 5 failed attempts, the batch is moved to `quarantined` with reason `processing_failed`. A retried batch is idempotent and never double-counts accepted readings.

### Late Data Recomputation Engine
When a batch arrives containing readings taken earlier than a previously recorded lifecycle transition (`taken_at < sensor.lifecycle_since`):
1. The engine executes a deterministic historical replay from the sensor's first reading up to the current clock.
2. If the recomputed current lifecycle differs from the recorded state (e.g. sensor was prematurely placed in `dormant` because quiet days were reset by the backlogged reading), the engine appends a single immutable `correction` entry to `TimelineEntry` with `kind: "correction"` and references the causing `evidence_id`.
3. Earlier timeline entries remain intact. `lifecycle_since` is updated to the effective timestamp of the latest transition in the recomputed history.
4. Gateways are never recomputed backwards; gateway qualifying timestamps update forward only.

---

## 4. Interpretations of Ambiguities
* **Available Time Accumulation**: Dormant waits (14 days) and sampling windows (72 hours) require `available` coverage. When coverage drops to `stopped` or `recoverable`, the timer pauses and `next_evaluation_at` is set to null.
* **Rogue Readings During Stop**: A stop period runs from the `acked_at` of a stop command until the `acked_at` of a superseding resume command. Any reading taken within this interval raises `collecting_after_stop`.
* **DRF Status Codes**: The specification mandated 422 for invalid bodies and 409 for conflicts. A custom DRF exception handler (`monitor/exceptions.py`) translates validation errors to 422 and conflict exceptions to 409.

---

## 5. Trade-offs & Known Limitations
1. **Queue Architecture**: We utilized a direct DB-backed deterministic task processor inside the application rather than Celery + Redis. This guarantees 100% deterministic testing under `/test/clock` and `/test/drain` without distributed race conditions.
2. **Day Classification Complexity**: Full historical replay traverses completed UTC days iteratively. For high-volume networks spanning decades, event-sourced snapshotting would be required to avoid $O(N)$ day iteration.
