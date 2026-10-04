from datetime import datetime, timedelta, timezone, date
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone as dj_timezone

from .models import (
    Gateway, Sensor, GatewaySensorAssignment, Command,
    Batch, Reading, CycleReport, CycleResult, TimelineEntry, SystemState
)
from .clock import get_now, format_iso, parse_iso
from .exceptions import APIConflict, APIValidationError


VALID_UNITS = {
    'temperature': 'C',
    'rain': 'mm',
    'wind': 'm/s'
}


def record_timeline(entity, kind, axis, from_state, to_state, effective_at, rule=None, evidence_ids=None):
    """
    Appends an immutable timeline entry for Gateway or Sensor.
    Entries are never edited or deleted.
    """
    now = get_now()
    if evidence_ids is None:
        evidence_ids = []
    else:
        evidence_ids = [str(x) for x in evidence_ids]

    is_gateway = isinstance(entity, Gateway)
    gw = entity if is_gateway else None
    sensor = entity if not is_gateway else None

    last_entry = TimelineEntry.objects.filter(
        gateway=gw, sensor=sensor
    ).order_by('-seq').first()
    next_seq = (last_entry.seq + 1) if last_entry else 1

    entry = TimelineEntry.objects.create(
        gateway=gw,
        sensor=sensor,
        seq=next_seq,
        kind=kind,
        axis=axis,
        from_state=from_state,
        to_state=to_state,
        effective_at=effective_at,
        recorded_at=now,
        rule=rule,
        evidence_ids=evidence_ids
    )
    return entry


def compute_gateway_coverage_class(gateway: Gateway) -> str:
    return gateway.coverage_class


def compute_sensor_coverage(sensor: Sensor) -> str:
    assignments = GatewaySensorAssignment.objects.filter(sensor=sensor).select_related('gateway')
    if not assignments.exists():
        return 'none'

    classes = [a.gateway.coverage_class for a in assignments]
    if 'available' in classes:
        return 'available'
    if 'stopped' in classes:
        return 'stopped'
    if 'recoverable' in classes:
        return 'recoverable'
    return 'none'


def compute_sensor_collection(sensor: Sensor) -> str:
    sensor_cov = compute_sensor_coverage(sensor)
    now = get_now()
    cutoff_24h = now - timedelta(hours=24)

    assigned_gateways = [a.gateway for a in GatewaySensorAssignment.objects.filter(sensor=sensor).select_related('gateway')]
    available_gateways = [gw for gw in assigned_gateways if gw.coverage_class == 'available']

    outcomes = []
    for gw in available_gateways:
        latest_res = CycleResult.objects.filter(
            sensor_id=sensor.sensor_id,
            cycle__gateway=gw,
            cycle__finished_at__gte=cutoff_24h,
            cycle__finished_at__lte=now
        ).select_related('cycle').order_by('-cycle__finished_at').first()

        if latest_res:
            outcome_val = latest_res.outcome
            if outcome_val == 'timeout':
                outcome_val = 'timed_out'
            outcomes.append(outcome_val)

        orphan_reading = Reading.objects.filter(
            sensor=sensor,
            gateway=gw,
            accepted=True,
            taken_at__gte=cutoff_24h,
            taken_at__lte=now
        ).order_by('-taken_at').first()

        if orphan_reading:
            is_in_cycle = CycleResult.objects.filter(batch_id=orphan_reading.batch.batch_id).exists()
            if not is_in_cycle:
                outcomes.append('readings')

    if outcomes:
        for p in ['readings', 'no_readings', 'could_not_read', 'timed_out']:
            if p in outcomes:
                return p

    if sensor_cov == 'stopped':
        return 'collection_stopped'
    return 'not_checked'


def recompute_coverage_for_sensor(sensor: Sensor, now: datetime = None):
    """
    Recomputes coverage and handles sensor transitions caused by coverage changes:
    - If sensor coverage becomes 'none':
      'pending' and 'decommissioned' stay; any other state becomes 'retired' with reason 'no_live_coverage' at once!
    - A retired ('no_live_coverage') sensor becomes 'pending' as soon as its coverage is anything other than 'none'.
      Its counters reset.
    """
    if now is None:
        now = get_now()

    new_coverage = compute_sensor_coverage(sensor)

    if new_coverage == 'none':
        if sensor.lifecycle not in ['pending', 'decommissioned', 'retired']:
            old_lifecycle = sensor.lifecycle
            sensor.lifecycle = 'retired'
            sensor.lifecycle_reason = 'no_live_coverage'
            sensor.lifecycle_since = now
            sensor.next_evaluation_at = None
            sensor.save()
            record_timeline(
                entity=sensor,
                kind='transition',
                axis='lifecycle',
                from_state=old_lifecycle,
                to_state='retired',
                effective_at=now,
                rule='loss_of_coverage'
            )
    else:
        # Coverage is restored (anything other than none)
        if sensor.lifecycle == 'retired' and sensor.lifecycle_reason == 'no_live_coverage':
            old_lifecycle = sensor.lifecycle
            sensor.lifecycle = 'pending'
            sensor.lifecycle_reason = None
            sensor.lifecycle_since = now
            sensor.quiet_checked_days = 0
            sensor.sampling_cycles_done = 0
            sensor.next_evaluation_at = None
            sensor.save()
            record_timeline(
                entity=sensor,
                kind='transition',
                axis='lifecycle',
                from_state=old_lifecycle,
                to_state='pending',
                effective_at=now,
                rule='coverage_restored'
            )


def evaluate_gateway_transitions(gateway: Gateway, now: datetime = None):
    if now is None:
        now = get_now()

    # 1. Command timeouts (10 minutes on server clock)
    if gateway.command_state in ['stop_pending', 'resume_pending']:
        latest_cmd = gateway.commands.filter(acknowledged=False).order_by('-seq').first()
        if latest_cmd and (now - latest_cmd.issued_at) >= timedelta(minutes=10):
            old_state = gateway.command_state
            new_state = 'stop_failed' if old_state == 'stop_pending' else 'resume_failed'
            gateway.command_state = new_state
            gateway.save()
            record_timeline(
                entity=gateway,
                kind='transition',
                axis='command_state',
                from_state=old_state,
                to_state=new_state,
                effective_at=latest_cmd.issued_at + timedelta(minutes=10),
                rule='command_timeout',
                evidence_ids=[latest_cmd.command_id]
            )

    # 2. Gateway stale check (12 hours since last_qualifying_at)
    if gateway.status == 'connected':
        if gateway.last_qualifying_at:
            time_since_qualifying = now - gateway.last_qualifying_at
            if time_since_qualifying > timedelta(hours=12):
                old_status = gateway.status
                gateway.status = 'stale'
                stale_effective_at = gateway.last_qualifying_at + timedelta(hours=12)
                gateway.status_since = stale_effective_at
                gateway.save()
                record_timeline(
                    entity=gateway,
                    kind='transition',
                    axis='status',
                    from_state=old_status,
                    to_state='stale',
                    effective_at=stale_effective_at,
                    rule='12_hour_stale'
                )


def on_qualifying_evidence(gateway: Gateway, evidence_time: datetime, evidence_id: str):
    """
    Applies qualifying evidence rules to gateway:
    - new -> connected
    - stale -> connected (if evidence_time within last 12 hours)
    - disconnected -> connected (if evidence_time after disconnected_since)
    """
    now = get_now()
    if gateway.status == 'retired':
        return

    # Update last_qualifying_at only when evidence_time is newer than current value
    if gateway.last_qualifying_at is None or evidence_time > gateway.last_qualifying_at:
        gateway.last_qualifying_at = evidence_time

    old_status = gateway.status

    if old_status == 'new':
        gateway.status = 'connected'
        gateway.status_since = evidence_time
        gateway.save()
        record_timeline(gateway, 'transition', 'status', old_status, 'connected', evidence_time, 'qualifying_evidence', [evidence_id])

    elif old_status == 'stale':
        if (now - evidence_time) <= timedelta(hours=12):
            gateway.status = 'connected'
            gateway.status_since = evidence_time
            gateway.save()
            record_timeline(gateway, 'transition', 'status', old_status, 'connected', evidence_time, 'stale_recovery', [evidence_id])

    elif old_status == 'disconnected':
        if gateway.disconnected_since and evidence_time > gateway.disconnected_since:
            gateway.status = 'connected'
            gateway.status_since = evidence_time
            gateway.disconnected_since = None
            gateway.save()
            record_timeline(gateway, 'transition', 'status', old_status, 'connected', evidence_time, 'disconnected_recovery', [evidence_id])

    gateway.save()


def process_batch(batch: Batch):
    """
    Processes a batch in the background or during clock sync/drain.
    Follows all validation, quarantine, retry, and duplicate rules.
    """
    now = get_now()
    batch.attempts += 1

    # Check for injected test faults
    state = SystemState.get_state()
    if state.processing_failures_remaining > 0:
        state.processing_failures_remaining -= 1
        state.save()
        if batch.attempts >= 5:
            batch.processing = 'quarantined'
            batch.quarantine_reason = 'processing_failed'
            batch.save()
        else:
            batch.processing = 'retrying'
            delay_minutes = 2 ** (batch.attempts - 1)
            batch.next_attempt_at = now + timedelta(minutes=delay_minutes)
            batch.save()
        return

    raw_payload = batch.raw_payload
    sensor_id = raw_payload.get('sensor_id')
    readings_data = raw_payload.get('readings', [])

    sensor = Sensor.objects.filter(sensor_id=sensor_id).first()
    if not sensor:
        batch.processing = 'quarantined'
        batch.quarantine_reason = 'unknown_sensor'
        batch.save()
        return

    batch.sensor = sensor
    expected_unit = VALID_UNITS.get(sensor.type)

    accepted_readings = []
    quarantined_items = []
    has_conflicting_dup = False

    for r in readings_data:
        r_id = r.get('reading_id')
        val = r.get('value')
        unit = r.get('unit')
        taken_at_str = r.get('taken_at')

        # Check required fields and number
        if not r_id or val is None or not isinstance(val, (int, float)) or not unit or not taken_at_str:
            quarantined_items.append({'reading_id': r_id or 'unknown', 'reason': 'invalid_fields'})
            continue

        taken_at = parse_iso(taken_at_str)

        # Device timestamp > 5 mins after batch received_at is invalid
        if taken_at > (batch.received_at + timedelta(minutes=5)):
            quarantined_items.append({'reading_id': r_id, 'reason': 'timestamp_in_future'})
            continue

        # Check unit matches sensor type
        if expected_unit and unit != expected_unit:
            quarantined_items.append({'reading_id': r_id, 'reason': 'invalid_unit'})
            continue

        # Check uniqueness of reading_id
        existing_reading = Reading.objects.filter(reading_id=r_id).first()
        if existing_reading:
            # Check content match
            if (existing_reading.sensor_id == sensor.sensor_id and
                existing_reading.value == float(val) and
                existing_reading.unit == unit and
                existing_reading.taken_at == taken_at):
                # Identical duplicate -> ignored
                continue
            else:
                quarantined_items.append({'reading_id': r_id, 'reason': 'conflicting_duplicate'})
                has_conflicting_dup = True
                continue

        accepted_readings.append({
            'reading_id': r_id,
            'taken_at': taken_at,
            'value': float(val),
            'unit': unit
        })

    # Determine batch state
    if len(accepted_readings) == 0 and len(quarantined_items) > 0:
        batch.processing = 'quarantined'
        batch.quarantine_reason = quarantined_items[0]['reason']
    elif len(quarantined_items) > 0:
        batch.processing = 'partially_processed'
    else:
        batch.processing = 'processed'

    batch.accepted_count = len(accepted_readings)
    batch.quarantined = quarantined_items
    batch.processed_at = now
    batch.save()

    # Create Reading objects
    for ar in accepted_readings:
        Reading.objects.create(
            reading_id=ar['reading_id'],
            batch=batch,
            gateway=batch.gateway,
            sensor=sensor,
            taken_at=ar['taken_at'],
            value=ar['value'],
            unit=ar['unit'],
            accepted=True,
            created_at=now
        )

        # Check collecting-after-stop flag
        gw = batch.gateway
        if gw.stop_period_acked_at:
            is_inside_stop = False
            if gw.resume_period_acked_at is None:
                is_inside_stop = (ar['taken_at'] >= gw.stop_period_acked_at)
            else:
                is_inside_stop = (gw.stop_period_acked_at <= ar['taken_at'] <= gw.resume_period_acked_at)

            if is_inside_stop and not gw.has_collecting_after_stop:
                gw.has_collecting_after_stop = True
                gw.save()
                record_timeline(
                    entity=gw,
                    kind='flag',
                    axis='flag',
                    from_state=None,
                    to_state='collecting_after_stop',
                    effective_at=ar['taken_at'],
                    rule='reading_inside_stop_period',
                    evidence_ids=[ar['reading_id']]
                )

    # If any reading was accepted, this is qualifying evidence for the gateway!
    if accepted_readings:
        latest_taken_at = max(r['taken_at'] for r in accepted_readings)
        on_qualifying_evidence(batch.gateway, latest_taken_at, batch.batch_id)
        # Check if any reading arrived earlier than transition (for non-pending sensor)
        if sensor.lifecycle != 'pending' and sensor.lifecycle_since and any(r['taken_at'] < sensor.lifecycle_since for r in accepted_readings):
            recompute_sensor_lifecycle(sensor, min(r['taken_at'] for r in accepted_readings), batch.batch_id)
        else:
            evaluate_sensor(sensor)


def classify_sensor_day(sensor: Sensor, day_date: date) -> str:
    """
    Days are UTC dates. Evaluated at 00:00 UTC next day.
    Classifications (first match wins):
    1. Reading day: at least one accepted reading taken that day (from any gateway).
    2. Unresolved day: readings outcome points to a batch not yet resolved.
    3. Quiet day: at least one no_readings outcome that day from an available gateway.
    4. Unchecked day: everything else (could_not_read, timed_out, silence).
    """
    start_utc = datetime(day_date.year, day_date.month, day_date.day, 0, 0, 0, tzinfo=timezone.utc)
    end_utc = start_utc + timedelta(days=1)

    # 1. Reading day
    has_accepted_reading = Reading.objects.filter(
        sensor=sensor,
        accepted=True,
        taken_at__gte=start_utc,
        taken_at__lt=end_utc
    ).exists()
    if has_accepted_reading:
        return 'reading'

    # Check cycles finished on that day
    cycles = CycleResult.objects.filter(
        sensor_id=sensor.sensor_id,
        cycle__finished_at__gte=start_utc,
        cycle__finished_at__lt=end_utc
    ).select_related('cycle', 'cycle__gateway')

    # 2. Check for unresolved batches
    for res in cycles:
        if res.outcome == 'readings' and res.batch_id:
            batch = Batch.objects.filter(batch_id=res.batch_id).first()
            if not batch:
                now = get_now()
                if (now - res.cycle.finished_at) < timedelta(hours=24):
                    return 'unresolved'
            else:
                if batch.processing in ['received', 'retrying']:
                    now = get_now()
                    if (now - res.cycle.finished_at) < timedelta(hours=24):
                        return 'unresolved'

    # 3. Quiet day: >= 1 no_readings outcome that day from a gateway whose coverage class was available at that time
    for res in cycles:
        if res.outcome == 'no_readings' and res.cycle.session == 'ok':
            gw = res.cycle.gateway
            if gw.status not in ['suspended', 'retired']:
                return 'quiet'

    # 4. Unchecked day
    return 'unchecked'


def evaluate_sensor(sensor: Sensor):
    """
    Evaluates sensor transitions up to now:
    1. decommission check
    2. coverage check
    3. readings check (pending -> active, dormant -> active, sampling -> active)
    4. timer / day transitions
    """
    now = get_now()
    coverage = compute_sensor_coverage(sensor)

    if sensor.lifecycle == 'decommissioned':
        return

    # Loss of coverage
    if coverage == 'none':
        if sensor.lifecycle not in ['pending', 'retired']:
            old = sensor.lifecycle
            sensor.lifecycle = 'retired'
            sensor.lifecycle_reason = 'no_live_coverage'
            sensor.lifecycle_since = now
            sensor.next_evaluation_at = None
            sensor.save()
            record_timeline(sensor, 'transition', 'lifecycle', old, 'retired', now, 'loss_of_coverage')
        return

    # Check accepted readings
    accepted_readings = Reading.objects.filter(sensor=sensor, accepted=True).order_by('taken_at')
    first_reading = accepted_readings.first()

    if sensor.lifecycle == 'pending' and first_reading:
        old = sensor.lifecycle
        sensor.lifecycle = 'active'
        sensor.lifecycle_since = first_reading.taken_at
        sensor.quiet_checked_days = 0
        sensor.sampling_cycles_done = 0
        sensor.save()
        record_timeline(sensor, 'transition', 'lifecycle', old, 'active', first_reading.taken_at, 'first_reading', [first_reading.reading_id])

    # 1. Active sensor logic
    if sensor.lifecycle == 'active':
        # Check awake from dormant / retired
        # Count complete days since latest reading day or start
        current_date = now.date()
        # Days completed are strictly before current_date (i.e. <= current_date - 1)
        eval_start_date = sensor.lifecycle_since.date()
        
        # Traverse each day from eval_start_date to current_date - timedelta(days=1)
        day_cursor = eval_start_date
        quiet_count = sensor.quiet_checked_days
        became_dormant = False
        dormant_effective = None

        while day_cursor < current_date:
            classification = classify_sensor_day(sensor, day_cursor)
            if classification == 'reading':
                quiet_count = 0
            elif classification == 'quiet':
                quiet_count += 1
                if quiet_count >= 14:
                    # The 14th quiet day completes at 00:00 UTC of next day
                    dormant_effective = datetime(day_cursor.year, day_cursor.month, day_cursor.day, 0, 0, 0, tzinfo=timezone.utc) + timedelta(days=1)
                    became_dormant = True
                    break
            elif classification == 'unresolved':
                # Wait until day resolves
                break
            # unchecked days neither count nor reset
            day_cursor += timedelta(days=1)

        sensor.quiet_checked_days = quiet_count

        if became_dormant and dormant_effective <= now:
            old = sensor.lifecycle
            sensor.lifecycle = 'dormant'
            sensor.lifecycle_since = dormant_effective
            sensor.wait_window_started_at = dormant_effective
            sensor.available_seconds_accumulated = 0.0
            # Next evaluation at is 14 days of available time
            sensor.next_evaluation_at = dormant_effective + timedelta(days=14)
            sensor.save()
            record_timeline(sensor, 'transition', 'lifecycle', old, 'dormant', dormant_effective, '14_quiet_days')

    # 2. Dormant sensor logic
    if sensor.lifecycle == 'dormant':
        # Check if reading wakes it
        latest_reading = accepted_readings.last()
        if latest_reading and latest_reading.taken_at > sensor.lifecycle_since:
            old = sensor.lifecycle
            sensor.lifecycle = 'active'
            sensor.lifecycle_since = latest_reading.taken_at
            sensor.lifecycle_reason = None
            sensor.quiet_checked_days = 0
            sensor.sampling_cycles_done = 0
            sensor.wait_window_started_at = None
            sensor.next_evaluation_at = None
            sensor.save()
            record_timeline(sensor, 'transition', 'lifecycle', old, 'active', latest_reading.taken_at, 'reading_wake', [latest_reading.reading_id])
            return

        # Check 14 days of available time
        if coverage == 'available':
            if sensor.wait_window_started_at is None:
                sensor.wait_window_started_at = sensor.lifecycle_since
            # Check elapsed available time
            time_in_dormant = (now - sensor.wait_window_started_at).total_seconds()
            required_seconds = 14 * 86400
            if time_in_dormant >= required_seconds:
                sampling_effective = sensor.wait_window_started_at + timedelta(days=14)
                old = sensor.lifecycle
                sensor.lifecycle = 'sampling'
                sensor.lifecycle_since = sampling_effective
                sensor.wait_window_started_at = sampling_effective
                sensor.available_seconds_accumulated = 0.0
                sensor.next_evaluation_at = sampling_effective + timedelta(hours=72)
                sensor.save()
                record_timeline(sensor, 'transition', 'lifecycle', old, 'sampling', sampling_effective, 'dormant_wait_completed')

    # 3. Sampling sensor logic
    if sensor.lifecycle == 'sampling':
        # Check if reading inside window wakes it
        window_start = sensor.wait_window_started_at or sensor.lifecycle_since
        window_end = window_start + timedelta(hours=72)

        reading_in_window = accepted_readings.filter(taken_at__gte=window_start, taken_at__lte=window_end).first()
        if reading_in_window:
            old = sensor.lifecycle
            sensor.lifecycle = 'active'
            sensor.lifecycle_since = reading_in_window.taken_at
            sensor.quiet_checked_days = 0
            sensor.sampling_cycles_done = 0
            sensor.wait_window_started_at = None
            sensor.next_evaluation_at = None
            sensor.save()
            record_timeline(sensor, 'transition', 'lifecycle', old, 'active', reading_in_window.taken_at, 'sampling_reading', [reading_in_window.reading_id])
            return

        # Check window end
        if coverage == 'available' and now >= window_end:
            # Check if window had >= 1 no_readings outcome from an available gateway
            had_no_readings = CycleResult.objects.filter(
                sensor_id=sensor.sensor_id,
                cycle__finished_at__gte=window_start,
                cycle__finished_at__lte=window_end,
                outcome='no_readings'
            ).exists()

            if had_no_readings:
                sensor.sampling_cycles_done += 1
                if sensor.sampling_cycles_done >= 2:
                    old = sensor.lifecycle
                    sensor.lifecycle = 'retired'
                    sensor.lifecycle_reason = 'no_readings'
                    sensor.lifecycle_since = window_end
                    sensor.next_evaluation_at = None
                    sensor.save()
                    record_timeline(sensor, 'transition', 'lifecycle', old, 'retired', window_end, 'sampling_cycle_exhausted')
                else:
                    old = sensor.lifecycle
                    sensor.lifecycle = 'dormant'
                    sensor.lifecycle_since = window_end
                    sensor.wait_window_started_at = window_end
                    sensor.available_seconds_accumulated = 0.0
                    sensor.next_evaluation_at = window_end + timedelta(days=14)
                    sensor.save()
                    record_timeline(sensor, 'transition', 'lifecycle', old, 'dormant', window_end, 'sampling_window_no_readings')
            else:
                # Window ends with no no_readings outcome inside it. Does not count as a cycle.
                sensor.wait_window_started_at = window_end
                sensor.next_evaluation_at = window_end + timedelta(hours=72)
                sensor.save()
                record_timeline(sensor, 'transition', 'lifecycle', 'sampling', 'sampling', window_end, 'sampling_new_window')

    sensor.save()


def recompute_sensor_lifecycle(sensor: Sensor, from_time: datetime, evidence_id: str = None):
    """
    Deterministic full recompute of sensor history when late data arrives.
    If recomputed history differs from recorded transitions,
    appends one correction entry to timeline and updates sensor state.
    """
    now = get_now()
    coverage = compute_sensor_coverage(sensor)

    if sensor.lifecycle == 'decommissioned':
        return

    old_state = sensor.lifecycle

    # Recompute history from beginning
    all_readings = Reading.objects.filter(sensor=sensor, accepted=True).order_by('taken_at')
    if not all_readings.exists():
        new_state = 'pending'
        new_since = sensor.lifecycle_since
        new_quiet = 0
    else:
        first_r = all_readings.first()
        curr_state = 'active'
        curr_since = first_r.taken_at
        quiet_count = 0
        window_start = None

        cursor = first_r.taken_at.date()
        today = now.date()

        while cursor < today:
            cls = classify_sensor_day(sensor, cursor)
            if curr_state == 'active':
                if cls == 'reading':
                    quiet_count = 0
                    curr_since = datetime(cursor.year, cursor.month, cursor.day, 12, 0, 0, tzinfo=timezone.utc)
                elif cls == 'quiet':
                    quiet_count += 1
                    if quiet_count >= 14:
                        curr_state = 'dormant'
                        curr_since = datetime(cursor.year, cursor.month, cursor.day, 0, 0, 0, tzinfo=timezone.utc) + timedelta(days=1)
                        window_start = curr_since
            elif curr_state == 'dormant':
                if cls == 'reading':
                    curr_state = 'active'
                    quiet_count = 0
                    curr_since = datetime(cursor.year, cursor.month, cursor.day, 12, 0, 0, tzinfo=timezone.utc)
            cursor += timedelta(days=1)

        new_state = curr_state
        new_since = curr_since
        new_quiet = quiet_count

    if old_state != new_state:
        sensor.lifecycle = new_state
        sensor.lifecycle_since = new_since
        sensor.quiet_checked_days = new_quiet
        if new_state == 'active':
            sensor.next_evaluation_at = None
        sensor.save()
        ev_ids = [evidence_id] if evidence_id else []
        record_timeline(
            entity=sensor,
            kind='correction',
            axis='lifecycle',
            from_state=old_state,
            to_state=new_state,
            effective_at=new_since,
            rule='late_data_correction',
            evidence_ids=ev_ids
        )


def advance_time_and_drain(target_time: datetime = None):
    """
    Executes all pending batches, retries, and time-based transitions
    up to target_time in deterministic event-time order.
    """
    now = target_time if target_time is not None else get_now()

    # 1. Process all pending batches and retries due up to now
    pending_batches = Batch.objects.filter(
        processing__in=['received', 'retrying']
    ).filter(
        models.Q(next_attempt_at__isnull=True) | models.Q(next_attempt_at__lte=now)
    ).order_by('received_at')

    for batch in pending_batches:
        process_batch(batch)

    # 2. Evaluate all gateways for timeouts / stale transitions
    for gw in Gateway.objects.all():
        evaluate_gateway_transitions(gw, now)

    # 3. Evaluate all sensors
    for sensor in Sensor.objects.all():
        evaluate_sensor(sensor)
