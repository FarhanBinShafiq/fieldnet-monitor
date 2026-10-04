#!/usr/bin/env python3
"""
FieldNet Gateway Simulator CLI
Plays three realistic failure stories through the fixed API and test clock,
each ending in assertions:
1. Gateway Auth Failure and Recovery
2. Rogue Reading During Stop Period (collecting_after_stop flag)
3. Late Backlogged Data & Historical Replay Correction
"""

import sys
import requests
from datetime import datetime, timedelta, timezone

DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def log(msg):
    print(f"[SIMULATOR] {msg}")


class SimulatorClient:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip('/')

    def post(self, path, json=None, headers=None):
        url = f"{self.base_url}{path}"
        r = requests.post(url, json=json, headers=headers)
        return r

    def get(self, path, headers=None):
        url = f"{self.base_url}{path}"
        r = requests.get(url, headers=headers)
        return r

    def put(self, path, json=None, headers=None):
        url = f"{self.base_url}{path}"
        r = requests.put(url, json=json, headers=headers)
        return r

    def reset(self):
        r = self.post('/test/reset')
        assert r.status_code == 200, f"Reset failed: {r.text}"

    def set_clock(self, dt_iso):
        r = self.post('/test/clock', {'now': dt_iso})
        assert r.status_code == 200, f"Clock advance failed: {r.text}"


def story_1_auth_failure_and_recovery(client: SimulatorClient):
    log("=" * 60)
    log("STORY 1: Gateway Authentication Failure & Autonomous Recovery")
    log("=" * 60)

    client.reset()
    now = datetime(2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc)
    client.set_clock(now.isoformat())

    # 1. Register Gateway and Sensor
    r = client.post('/api/v1/gateways', {'gateway_id': 'gw-story-1', 'name': 'Desert Gateway'})
    assert r.status_code == 201
    token = r.json()['token']
    auth_header = {'Authorization': f'Bearer {token}'}

    client.post('/api/v1/sensors', {'sensor_id': 's-story-1', 'type': 'temperature'})
    client.put('/api/v1/sensors/s-story-1/coverage', {'gateway_ids': ['gw-story-1']})

    # Gateway sends good cycle -> becomes connected
    client.post('/gw/v1/cycles', {
        'cycle_id': 'c-s1-1',
        'started_at': now.isoformat(),
        'finished_at': now.isoformat(),
        'session': 'ok',
        'results': [{'sensor_id': 's-story-1', 'outcome': 'no_readings'}]
    }, headers=auth_header)

    gw = client.get('/api/v1/gateways/gw-story-1').json()
    assert gw['status'] == 'connected', f"Expected connected, got {gw['status']}"
    log("  Step 1: Gateway is CONNECTED and operating normally.")

    # 2. Network issue causes auth failure on heartbeat at 10:30
    fail_time = now + timedelta(minutes=30)
    client.set_clock(fail_time.isoformat())
    client.post('/gw/v1/heartbeat', {'sent_at': fail_time.isoformat(), 'session': 'auth_failed'}, headers=auth_header)

    gw = client.get('/api/v1/gateways/gw-story-1').json()
    assert gw['status'] == 'disconnected', f"Expected disconnected, got {gw['status']}"
    assert gw['disconnected_since'] is not None
    log("  Step 2: Heartbeat auth_failed occurred. Gateway transitioned to DISCONNECTED.")

    # 3. Old evidence sent after failure does NOT recover gateway
    old_time = fail_time - timedelta(minutes=10)
    client.post('/gw/v1/cycles', {
        'cycle_id': 'c-s1-old',
        'started_at': old_time.isoformat(),
        'finished_at': old_time.isoformat(),
        'session': 'ok',
        'results': [{'sensor_id': 's-story-1', 'outcome': 'no_readings'}]
    }, headers=auth_header)
    gw = client.get('/api/v1/gateways/gw-story-1').json()
    assert gw['status'] == 'disconnected', "Old cycle must not recover disconnected gateway"
    log("  Step 3: Old cycle from before failure rejected as recovery evidence. Gateway remains DISCONNECTED.")

    # 4. Fresh cycle at 11:00 with session: 'ok' recovers gateway!
    recover_time = now + timedelta(hours=1)
    client.set_clock(recover_time.isoformat())
    client.post('/gw/v1/cycles', {
        'cycle_id': 'c-s1-fresh',
        'started_at': recover_time.isoformat(),
        'finished_at': recover_time.isoformat(),
        'session': 'ok',
        'results': [{'sensor_id': 's-story-1', 'outcome': 'no_readings'}]
    }, headers=auth_header)

    gw = client.get('/api/v1/gateways/gw-story-1').json()
    assert gw['status'] == 'connected', f"Expected connected, got {gw['status']}"
    assert gw['disconnected_since'] is None
    log("  Step 4: Fresh qualifying cycle processed. Gateway successfully RECOVERED to CONNECTED.")
    log(">>> ASSERTION PASSED: Story 1 completed successfully!\n")


def story_2_stop_period_rogue_readings(client: SimulatorClient):
    log("=" * 60)
    log("STORY 2: Rogue Readings During Stop Period (collecting_after_stop)")
    log("=" * 60)

    client.reset()
    now = datetime(2026, 4, 1, 12, 0, 0, tzinfo=timezone.utc)
    client.set_clock(now.isoformat())

    # Register GW and Sensor
    r = client.post('/api/v1/gateways', {'gateway_id': 'gw-story-2', 'name': 'Valley Gateway'})
    token = r.json()['token']
    auth_header = {'Authorization': f'Bearer {token}'}

    client.post('/api/v1/sensors', {'sensor_id': 's-story-2', 'type': 'rain'})
    client.put('/api/v1/sensors/s-story-2/coverage', {'gateway_ids': ['gw-story-2']})

    # Initial batch to connect
    client.put('/gw/v1/batches/b-s2-init', {
        'sensor_id': 's-story-2',
        'readings': [{'reading_id': 'r-s2-init', 'taken_at': now.isoformat(), 'value': 5.0, 'unit': 'mm'}]
    }, headers=auth_header)

    # 1. Operator orders STOP
    client.post('/api/v1/gateways/gw-story-2/actions', {'action': 'stop'})
    cmds = client.get('/gw/v1/commands', headers=auth_header).json()['commands']
    cmd_id = cmds[0]['command_id']

    # 2. Gateway acks stop at 12:05
    ack_time = now + timedelta(minutes=5)
    client.post(f'/gw/v1/commands/{cmd_id}/ack', {'acked_at': ack_time.isoformat()}, headers=auth_header)

    gw = client.get('/api/v1/gateways/gw-story-2').json()
    assert gw['command_state'] == 'stopped'
    assert gw['flags'] == []
    log("  Step 1: Gateway acknowledged STOP. Command state is STOPPED.")

    # 3. Rogue gateway continues collecting and uploads reading taken at 12:10 (inside stop period)
    rogue_time = now + timedelta(minutes=10)
    client.set_clock((now + timedelta(minutes=15)).isoformat())
    client.put('/gw/v1/batches/b-s2-rogue', {
        'sensor_id': 's-story-2',
        'readings': [{'reading_id': 'r-s2-rogue', 'taken_at': rogue_time.isoformat(), 'value': 8.5, 'unit': 'mm'}]
    }, headers=auth_header)

    gw = client.get('/api/v1/gateways/gw-story-2').json()
    assert 'collecting_after_stop' in gw['flags'], f"Expected flag collecting_after_stop, got {gw['flags']}"
    log("  Step 2: Rogue reading accepted and counted. collecting_after_stop flag RAISED on gateway.")

    # 4. Operator issues resume, gateway acks it -> flag is cleared
    client.post('/api/v1/gateways/gw-story-2/actions', {'action': 'resume'})
    cmds = client.get('/gw/v1/commands', headers=auth_header).json()['commands']
    resume_id = cmds[0]['command_id']

    resume_ack_time = now + timedelta(minutes=20)
    client.post(f'/gw/v1/commands/{resume_id}/ack', {'acked_at': resume_ack_time.isoformat()}, headers=auth_header)

    gw = client.get('/api/v1/gateways/gw-story-2').json()
    assert gw['command_state'] == 'running'
    assert 'collecting_after_stop' not in gw['flags']
    log("  Step 3: RESUME acknowledged. Command state restored to RUNNING and flag CLEARED.")
    log(">>> ASSERTION PASSED: Story 2 completed successfully!\n")


def story_3_late_data_and_correction_engine(client: SimulatorClient):
    log("=" * 60)
    log("STORY 3: Late Data Arrival & Historical Recomputation Correction")
    log("=" * 60)

    client.reset()
    base_date = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc) # Day 0
    client.set_clock(base_date.isoformat())

    r = client.post('/api/v1/gateways', {'gateway_id': 'gw-story-3', 'name': 'Mountain Gateway'})
    token = r.json()['token']
    auth_header = {'Authorization': f'Bearer {token}'}

    client.post('/api/v1/sensors', {'sensor_id': 's-story-3', 'type': 'wind'})
    client.put('/api/v1/sensors/s-story-3/coverage', {'gateway_ids': ['gw-story-3']})

    # Day 0: reading -> active
    client.put('/gw/v1/batches/b-s3-d0', {
        'sensor_id': 's-story-3',
        'readings': [{'reading_id': 'r-s3-d0', 'taken_at': base_date.isoformat(), 'value': 12.0, 'unit': 'm/s'}]
    }, headers=auth_header)

    # Days 1 to 14: no_readings each day
    for day in range(1, 15):
        cycle_time = base_date + timedelta(days=day)
        client.set_clock(cycle_time.isoformat())
        client.post('/gw/v1/cycles', {
            'cycle_id': f'c3-d{day}',
            'started_at': (cycle_time - timedelta(minutes=5)).isoformat(),
            'finished_at': cycle_time.isoformat(),
            'session': 'ok',
            'results': [{'sensor_id': 's-story-3', 'outcome': 'no_readings'}]
        }, headers=auth_header)

    # Day 15, 00:00: S goes dormant
    day15_time = datetime(2026, 5, 16, 0, 0, 0, tzinfo=timezone.utc)
    client.set_clock(day15_time.isoformat())
    s = client.get('/api/v1/sensors/s-story-3').json()
    assert s['lifecycle'] == 'dormant'
    log("  Step 1: After 14 quiet days, sensor transitioned to DORMANT on Day 15.")

    # Days 15 to 19: cycles each day
    for day in range(15, 20):
        cycle_time = base_date + timedelta(days=day)
        client.set_clock(cycle_time.isoformat())
        client.post('/gw/v1/cycles', {
            'cycle_id': f'c3-d{day}',
            'started_at': (cycle_time - timedelta(minutes=5)).isoformat(),
            'finished_at': cycle_time.isoformat(),
            'session': 'ok',
            'results': [{'sensor_id': 's-story-3', 'outcome': 'no_readings'}]
        }, headers=auth_header)

    # Day 20, 12:00: G uploads late batch holding a reading taken on Day 8
    day20_time = base_date + timedelta(days=20)
    client.set_clock(day20_time.isoformat())

    day8_time = base_date + timedelta(days=8)
    client.put('/gw/v1/batches/b-s3-late', {
        'sensor_id': 's-story-3',
        'readings': [{'reading_id': 'r-s3-late-d8', 'taken_at': day8_time.isoformat(), 'value': 14.2, 'unit': 'm/s'}]
    }, headers=auth_header)

    # Sensor should be recomputed to ACTIVE!
    s = client.get('/api/v1/sensors/s-story-3').json()
    assert s['lifecycle'] == 'active', f"Expected active, got {s['lifecycle']}"
    log("  Step 2: Late batch from Day 8 arrived. Engine recomputed history and sensor is ACTIVE.")

    # Verify timeline contains exactly one correction entry and preserves day 15 dormant entry
    timeline = client.get('/api/v1/sensors/s-story-3/timeline').json()
    corrections = [e for e in timeline if e['kind'] == 'correction']
    assert len(corrections) == 1
    assert corrections[0]['from'] == 'dormant'
    assert corrections[0]['to'] == 'active'

    dormant_entries = [e for e in timeline if e.get('to') == 'dormant']
    assert len(dormant_entries) >= 1
    log("  Step 3: Timeline audit verified: immutable correction entry appended, historical entries preserved.")
    log(">>> ASSERTION PASSED: Story 3 completed successfully!\n")


def main():
    base_url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_BASE_URL
    log(f"Starting Gateway Simulator targeting: {base_url}")
    client = SimulatorClient(base_url)

    try:
        story_1_auth_failure_and_recovery(client)
        story_2_stop_period_rogue_readings(client)
        story_3_late_data_and_correction_engine(client)
        log("============================================================")
        log("ALL THREE SIMULATION STORIES COMPLETED WITH ZERO ERRORS!")
        log("============================================================")
    except Exception as e:
        log(f"SIMULATION FAILED: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
