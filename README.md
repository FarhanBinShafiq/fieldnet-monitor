# FieldNet Monitor

State Engine, Deterministic Simulator, and Operator Console for Unreliable Sensor Networks.

---

## Quickstart with Docker Compose

To launch the complete stack (PostgreSQL 16, Django Backend, and Next.js Frontend):

```bash
docker compose up --build
```

The services will become available at:
* **Operator Console (Frontend)**: [http://localhost:3000](http://localhost:3000)
* **Backend API**: [http://localhost:8000](http://localhost:8000)
* **PostgreSQL Database**: `localhost:5432`

---

## Running the Automated Tests

To execute the test suite (including the full 49-day worked example, multi-gateway coverage, command state machine, and late-data recomputation tests):

### Via Local Environment
```bash
pytest backend/monitor/tests/test_state_engine.py -v
```

### Via Docker
```bash
docker compose exec backend pytest monitor/tests/test_state_engine.py -v
```

---

## Running the Gateway Simulator CLI

The simulator plays three realistic failure scenarios through the fixed API and test clock with strict assertions:
1. **Story 1**: Gateway Auth Failure & Recovery
2. **Story 2**: Rogue Readings During Stop Period (`collecting_after_stop` flag)
3. **Story 3**: Late Data Arrival & Historical Recomputation Correction

### Via Local Environment
```bash
python backend/simulator.py http://127.0.0.1:8000
```

### Via Docker
```bash
docker compose exec backend python simulator.py http://127.0.0.1:8000
```

---

## API Summary

### Gateway Device API (`Authorization: Bearer <token>`)
* `POST /gw/v1/heartbeat`: `{sent_at, session}`
* `POST /gw/v1/cycles`: `{cycle_id, started_at, finished_at, session, results}`
* `PUT /gw/v1/batches/{batch_id}`: `{sensor_id, readings}`
* `GET /gw/v1/commands`: Returns latest unacknowledged command
* `POST /gw/v1/commands/{id}/ack`: `{acked_at}`

### Operator Console & State API
* `GET /api/v1/gateways` & `POST /api/v1/gateways`: List / register gateways
* `GET /api/v1/gateways/{id}`: Detailed gateway state & flags
* `POST /api/v1/gateways/{id}/actions`: `{action: suspend | unsuspend | mark_spare | retire | stop | resume, reason}`
* `GET /api/v1/gateways/{id}/timeline`: Immutable audit timeline
* `GET /api/v1/sensors` & `POST /api/v1/sensors`: List / register sensors
* `GET /api/v1/sensors/{id}`: Detailed sensor state, coverage, collection, next evaluation
* `PUT /api/v1/sensors/{id}/coverage`: `{gateway_ids}`
* `POST /api/v1/sensors/{id}/actions`: `{action: decommission, reason}`
* `GET /api/v1/sensors/{id}/timeline`: Immutable audit timeline
* `GET /api/v1/batches/{id}`: Batch status, attempts, quarantined details

### Test Mode Endpoints (`TEST_MODE=1`)
* `POST /test/reset`: Clears all DB tables
* `POST /test/clock`: `{now}` sets virtual server clock and executes due events
* `POST /test/drain`: Drains pending work without advancing clock
* `POST /test/faults`: `{processing_failures}` injects simulated processing failures
