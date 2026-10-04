# AI Tools & Usage Notes

### Tools Used
* **Antigravity AI (Pair Programming Agent)**: Used for scaffolding Django models, DRF serialization, writing unit test cases based on the PDF worked example, and designing Next.js components.

### What the AI Got Wrong (and Was Caught & Corrected)
1. **JSON Serialization of UUIDs in Timeline Evidence**:
   During initial implementation of the command acknowledgement flow, the AI directly passed `cmd.command_id` (a Python `UUID` object) into `evidence_ids` in PostgreSQL JSONField. This raised a `TypeError: Object of type UUID is not JSON serializable` in psycopg2 when creating the timeline entry. We caught this during test execution and sanitized all items in `evidence_ids` with `str(x)` before inserting into the timeline.
2. **Gateway Availability in Historical Day Classification**:
   The AI initially checked the *current* gateway coverage class when evaluating past completed days in `classify_sensor_day()`. This resulted in the 14-day dormant wait failing to advance because the gateway had turned stale at the moment of evaluation. We caught this and updated the check so that cycles sent with `session: "ok"` from gateways that were not suspended or retired at that time are properly recognized as available evidence during that historical day.
