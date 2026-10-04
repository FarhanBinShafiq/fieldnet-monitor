from datetime import datetime, timedelta, timezone
import pytest
from rest_framework.test import APIClient
from monitor.models import Gateway, Sensor, Batch, Reading, Command, TimelineEntry

@pytest.fixture
def client():
    return APIClient()

@pytest.fixture(autouse=True)
def reset_db(client):
    # Reset all tables before each test
    client.post('/test/reset')
    yield

@pytest.mark.django_db
def test_gateway_lifecycle_and_actions(client):
    # 1. Register gateway
    res = client.post('/api/v1/gateways', {'gateway_id': 'gw-1', 'name': 'Gateway One'}, format='json')
    assert res.status_code == 201
    token = res.data['token']
    assert token

    # 2. Duplicate gateway returns 409
    res_dup = client.post('/api/v1/gateways', {'gateway_id': 'gw-1', 'name': 'Duplicate'}, format='json')
    assert res_dup.status_code == 409

    # 3. Check gateway status is 'new'
    res_gw = client.get('/api/v1/gateways/gw-1')
    assert res_gw.status_code == 200
    assert res_gw.data['status'] == 'new'
    assert res_gw.data['coverage_class'] == 'recoverable'

    # 4. Mark spare when 0 sensors covered -> succeeds
    res_spare = client.post('/api/v1/gateways/gw-1/actions', {'action': 'mark_spare'}, format='json')
    assert res_spare.status_code == 200
    assert res_spare.data['status'] == 'spare'

    # 5. Register sensor and assign to gw-1 -> gateway becomes 'new' again
    client.post('/api/v1/sensors', {'sensor_id': 's-1', 'type': 'temperature'}, format='json')
    res_cov = client.put('/api/v1/sensors/s-1/coverage', {'gateway_ids': ['gw-1']}, format='json')
    assert res_cov.status_code == 200

    res_gw = client.get('/api/v1/gateways/gw-1')
    assert res_gw.data['status'] == 'new'

    # 6. Mark spare now returns 409 because it covers sensor s-1
    res_spare_fail = client.post('/api/v1/gateways/gw-1/actions', {'action': 'mark_spare'}, format='json')
    assert res_spare_fail.status_code == 409


@pytest.mark.django_db
def test_gateway_heartbeat_and_disconnect(client):
    # Register gateway
    res = client.post('/api/v1/gateways', {'gateway_id': 'gw-2', 'name': 'Gateway Two'}, format='json')
    token = res.data['token']

    # Set clock to 2026-10-01T12:00:00Z
    client.post('/test/clock', {'now': '2026-10-01T12:00:00Z'}, format='json')

    # Send heartbeat ok
    auth_header = f'Bearer {token}'
    hb_res = client.post('/gw/v1/heartbeat', {'sent_at': '2026-10-01T12:00:00Z', 'session': 'ok'},
                         HTTP_AUTHORIZATION=auth_header, format='json')
    assert hb_res.status_code == 204

    # Status remains 'new' (heartbeat ok does not qualify evidence)
    gw_res = client.get('/api/v1/gateways/gw-2')
    assert gw_res.data['status'] == 'new'
    assert gw_res.data['last_heartbeat_at'] == '2026-10-01T12:00:00Z'

    # Send heartbeat with auth_failed -> moves to disconnected
    hb_fail = client.post('/gw/v1/heartbeat', {'sent_at': '2026-10-01T12:05:00Z', 'session': 'auth_failed'},
                          HTTP_AUTHORIZATION=auth_header, format='json')
    assert hb_fail.status_code == 204

    gw_res = client.get('/api/v1/gateways/gw-2')
    assert gw_res.data['status'] == 'disconnected'
    assert gw_res.data['disconnected_since'] == '2026-10-01T12:05:00Z'


@pytest.mark.django_db
def test_stop_resume_and_collecting_after_stop(client):
    res_gw = client.post('/api/v1/gateways', {'gateway_id': 'gw-cmd', 'name': 'Command Gateway'}, format='json')
    token = res_gw.data['token']
    auth_header = f'Bearer {token}'

    client.post('/api/v1/sensors', {'sensor_id': 's-cmd', 'type': 'temperature'}, format='json')
    client.put('/api/v1/sensors/s-cmd/coverage', {'gateway_ids': ['gw-cmd']}, format='json')

    # Initial time
    client.post('/test/clock', {'now': '2026-10-01T10:00:00Z'}, format='json')

    # Qualify gateway with a batch
    batch_data = {
        'sensor_id': 's-cmd',
        'readings': [{
            'reading_id': 'r-initial',
            'taken_at': '2026-10-01T10:00:00Z',
            'value': 22.5,
            'unit': 'C'
        }]
    }
    client.put('/gw/v1/batches/b-init', batch_data, HTTP_AUTHORIZATION=auth_header, format='json')
    gw = client.get('/api/v1/gateways/gw-cmd').data
    assert gw['status'] == 'connected'
    assert gw['command_state'] == 'running'
    assert gw['coverage_class'] == 'available'

    # Operator issues stop
    stop_res = client.post('/api/v1/gateways/gw-cmd/actions', {'action': 'stop'}, format='json')
    assert stop_res.status_code == 200
    assert stop_res.data['command_state'] == 'stop_pending'

    # Gateway polls commands
    cmds_res = client.get('/gw/v1/commands', HTTP_AUTHORIZATION=auth_header)
    assert cmds_res.status_code == 200
    assert len(cmds_res.data['commands']) == 1
    cmd_id = cmds_res.data['commands'][0]['command_id']
    assert cmds_res.data['commands'][0]['type'] == 'stop'

    # Gateway acknowledges stop at 10:05
    ack_res = client.post(f'/gw/v1/commands/{cmd_id}/ack', {'acked_at': '2026-10-01T10:05:00Z'},
                          HTTP_AUTHORIZATION=auth_header, format='json')
    assert ack_res.status_code == 204

    gw = client.get('/api/v1/gateways/gw-cmd').data
    assert gw['command_state'] == 'stopped'
    assert gw['coverage_class'] == 'stopped'

    # Reading taken during stop period (at 10:10) -> raises collecting_after_stop flag!
    client.post('/test/clock', {'now': '2026-10-01T10:15:00Z'}, format='json')
    stop_batch = {
        'sensor_id': 's-cmd',
        'readings': [{
            'reading_id': 'r-violating',
            'taken_at': '2026-10-01T10:10:00Z',
            'value': 23.0,
            'unit': 'C'
        }]
    }
    client.put('/gw/v1/batches/b-violating', stop_batch, HTTP_AUTHORIZATION=auth_header, format='json')

    gw = client.get('/api/v1/gateways/gw-cmd').data
    assert 'collecting_after_stop' in gw['flags']

    # Operator issues resume
    client.post('/api/v1/gateways/gw-cmd/actions', {'action': 'resume'}, format='json')
    cmds_res = client.get('/gw/v1/commands', HTTP_AUTHORIZATION=auth_header)
    resume_cmd_id = cmds_res.data['commands'][0]['command_id']

    # Gateway acknowledges resume -> clears flag
    client.post(f'/gw/v1/commands/{resume_cmd_id}/ack', {'acked_at': '2026-10-01T10:20:00Z'},
                HTTP_AUTHORIZATION=auth_header, format='json')

    gw = client.get('/api/v1/gateways/gw-cmd').data
    assert gw['command_state'] == 'running'
    assert 'collecting_after_stop' not in gw['flags']


@pytest.mark.django_db
def test_worked_example_full_lifecycle(client):
    """
    Tests the complete 49-day lifecycle scenario from PDF Page 9:
    - Day 0: readings -> active
    - Days 1-14: no_readings -> dormant at Day 15 00:00
    - Day 29: 14 days available time -> sampling
    - Day 32: window ends -> dormant, sampling_cycles_done 1
    - Day 46: wait ends -> sampling
    - Day 49: window ends -> retired (no_readings)
    """
    # Setup Gateway and Sensor
    res_gw = client.post('/api/v1/gateways', {'gateway_id': 'G', 'name': 'Gateway G'}, format='json')
    token = res_gw.data['token']
    auth_header = f'Bearer {token}'

    client.post('/api/v1/sensors', {'sensor_id': 'S', 'type': 'temperature'}, format='json')
    client.put('/api/v1/sensors/S/coverage', {'gateway_ids': ['G']}, format='json')

    # Day 0, 12:00: readings
    client.post('/test/clock', {'now': '2026-01-01T12:00:00Z'}, format='json') # Day 0 12:00
    batch_d0 = {
        'sensor_id': 'S',
        'readings': [{
            'reading_id': 'r-d0',
            'taken_at': '2026-01-01T12:00:00Z',
            'value': 20.0,
            'unit': 'C'
        }]
    }
    client.put('/gw/v1/batches/b-d0', batch_d0, HTTP_AUTHORIZATION=auth_header, format='json')

    s = client.get('/api/v1/sensors/S').data
    assert s['lifecycle'] == 'active'

    # Days 1 to 14, 12:00: no_readings each day
    base_date = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    for day in range(1, 15):
        cycle_time = base_date + timedelta(days=day)
        time_iso = cycle_time.isoformat()
        client.post('/test/clock', {'now': time_iso}, format='json')
        cycle_data = {
            'cycle_id': f'cycle-d{day}',
            'started_at': (cycle_time - timedelta(minutes=5)).isoformat(),
            'finished_at': time_iso,
            'session': 'ok',
            'results': [{
                'sensor_id': 'S',
                'outcome': 'no_readings'
            }]
        }
        client.post('/gw/v1/cycles', cycle_data, HTTP_AUTHORIZATION=auth_header, format='json')

    # Advance to Day 15, 00:00:00Z
    day15_time = datetime(2026, 1, 16, 0, 0, 0, tzinfo=timezone.utc)
    client.post('/test/clock', {'now': day15_time.isoformat()}, format='json')

    s = client.get('/api/v1/sensors/S').data
    assert s['lifecycle'] == 'dormant'

    # Days 15 to 28, 12:00: G reports S once a day
    for day in range(15, 29):
        cycle_time = base_date + timedelta(days=day)
        client.post('/test/clock', {'now': cycle_time.isoformat()}, format='json')
        client.post('/gw/v1/cycles', {
            'cycle_id': f'cycle-d{day}',
            'started_at': (cycle_time - timedelta(minutes=5)).isoformat(),
            'finished_at': cycle_time.isoformat(),
            'session': 'ok',
            'results': [{'sensor_id': 'S', 'outcome': 'no_readings'}]
        }, HTTP_AUTHORIZATION=auth_header, format='json')

    # Advance to Day 29, 00:00:00Z (14 days of available time completed) -> sampling
    day29_time = datetime(2026, 1, 30, 0, 0, 0, tzinfo=timezone.utc)
    client.post('/test/clock', {'now': day29_time.isoformat()}, format='json')

    s = client.get('/api/v1/sensors/S').data
    assert s['lifecycle'] == 'sampling'

    # Days 29 to 31: no_readings each day
    for day in range(29, 32):
        cycle_time = base_date + timedelta(days=day)
        client.post('/test/clock', {'now': cycle_time.isoformat()}, format='json')
        client.post('/gw/v1/cycles', {
            'cycle_id': f'cycle-d{day}',
            'started_at': (cycle_time - timedelta(minutes=5)).isoformat(),
            'finished_at': cycle_time.isoformat(),
            'session': 'ok',
            'results': [{'sensor_id': 'S', 'outcome': 'no_readings'}]
        }, HTTP_AUTHORIZATION=auth_header, format='json')

    # Day 32, 00:00:00Z -> first sampling window ends with no readings -> dormant, sampling_cycles_done 1
    day32_time = datetime(2026, 2, 2, 0, 0, 0, tzinfo=timezone.utc)
    client.post('/test/clock', {'now': day32_time.isoformat()}, format='json')

    s = client.get('/api/v1/sensors/S').data
    assert s['lifecycle'] == 'dormant'
    assert s['sampling_cycles_done'] == 1

    # Days 32 to 45: G reports S once a day
    for day in range(32, 46):
        cycle_time = base_date + timedelta(days=day)
        client.post('/test/clock', {'now': cycle_time.isoformat()}, format='json')
        client.post('/gw/v1/cycles', {
            'cycle_id': f'cycle-d{day}',
            'started_at': (cycle_time - timedelta(minutes=5)).isoformat(),
            'finished_at': cycle_time.isoformat(),
            'session': 'ok',
            'results': [{'sensor_id': 'S', 'outcome': 'no_readings'}]
        }, HTTP_AUTHORIZATION=auth_header, format='json')

    # Day 46, 00:00:00Z -> 14 days pass -> sampling again
    day46_time = datetime(2026, 2, 16, 0, 0, 0, tzinfo=timezone.utc)
    client.post('/test/clock', {'now': day46_time.isoformat()}, format='json')

    s = client.get('/api/v1/sensors/S').data
    assert s['lifecycle'] == 'sampling'

    # Days 46 to 48: G reports S once a day
    for day in range(46, 49):
        cycle_time = base_date + timedelta(days=day)
        client.post('/test/clock', {'now': cycle_time.isoformat()}, format='json')
        client.post('/gw/v1/cycles', {
            'cycle_id': f'cycle-d{day}',
            'started_at': (cycle_time - timedelta(minutes=5)).isoformat(),
            'finished_at': cycle_time.isoformat(),
            'session': 'ok',
            'results': [{'sensor_id': 'S', 'outcome': 'no_readings'}]
        }, HTTP_AUTHORIZATION=auth_header, format='json')

    # Day 49, 00:00:00Z -> Second window ends with no readings -> retired (no_readings)
    day49_time = datetime(2026, 2, 19, 0, 0, 0, tzinfo=timezone.utc)
    client.post('/test/clock', {'now': day49_time.isoformat()}, format='json')

    s = client.get('/api/v1/sensors/S').data
    assert s['lifecycle'] == 'retired'
    assert s['lifecycle_reason'] == 'no_readings'


@pytest.mark.django_db
def test_command_timeout_to_failed(client):
    res_gw = client.post('/api/v1/gateways', {'gateway_id': 'gw-timeout', 'name': 'Timeout Gateway'}, format='json')
    token = res_gw.data['token']

    # Initial time
    client.post('/test/clock', {'now': '2026-05-01T10:00:00Z'}, format='json')

    # Operator issues stop
    client.post('/api/v1/gateways/gw-timeout/actions', {'action': 'stop'}, format='json')
    gw = client.get('/api/v1/gateways/gw-timeout').data
    assert gw['command_state'] == 'stop_pending'

    # 10 minutes pass without ack -> command state becomes stop_failed
    client.post('/test/clock', {'now': '2026-05-01T10:10:00Z'}, format='json')
    gw = client.get('/api/v1/gateways/gw-timeout').data
    assert gw['command_state'] == 'stop_failed'


@pytest.mark.django_db
def test_batch_validation_and_quarantine(client):
    res_gw = client.post('/api/v1/gateways', {'gateway_id': 'gw-batch', 'name': 'Batch Gateway'}, format='json')
    token = res_gw.data['token']
    auth_header = f'Bearer {token}'

    client.post('/api/v1/sensors', {'sensor_id': 's-temp', 'type': 'temperature'}, format='json')
    client.put('/api/v1/sensors/s-temp/coverage', {'gateway_ids': ['gw-batch']}, format='json')

    # Clock at 12:00
    client.post('/test/clock', {'now': '2026-06-01T12:00:00Z'}, format='json')

    # 1. Invalid unit (temperature requires C, but sending mm)
    batch_bad_unit = {
        'sensor_id': 's-temp',
        'readings': [{
            'reading_id': 'r-bad-unit',
            'taken_at': '2026-06-01T12:00:00Z',
            'value': 25.0,
            'unit': 'mm' # invalid unit for temperature
        }]
    }
    client.put('/gw/v1/batches/b-bad-unit', batch_bad_unit, HTTP_AUTHORIZATION=auth_header, format='json')
    b = client.get('/api/v1/batches/b-bad-unit').data
    assert b['processing'] == 'quarantined'
    assert b['accepted_count'] == 0

    # 2. Reading timestamp > 5 mins in future relative to batch received_at
    batch_future = {
        'sensor_id': 's-temp',
        'readings': [{
            'reading_id': 'r-future',
            'taken_at': '2026-06-01T12:06:00Z', # 6 mins in future
            'value': 25.0,
            'unit': 'C'
        }]
    }
    client.put('/gw/v1/batches/b-future', batch_future, HTTP_AUTHORIZATION=auth_header, format='json')
    b = client.get('/api/v1/batches/b-future').data
    assert b['processing'] == 'quarantined'

    # 3. Conflicting duplicate reading id with different value
    batch_valid = {
        'sensor_id': 's-temp',
        'readings': [{
            'reading_id': 'r-valid-1',
            'taken_at': '2026-06-01T12:00:00Z',
            'value': 25.0,
            'unit': 'C'
        }]
    }
    client.put('/gw/v1/batches/b-valid-1', batch_valid, HTTP_AUTHORIZATION=auth_header, format='json')
    b = client.get('/api/v1/batches/b-valid-1').data
    assert b['processing'] == 'processed'
    assert b['accepted_count'] == 1

    # Conflicting batch with same reading_id but value 30.0
    batch_conflict = {
        'sensor_id': 's-temp',
        'readings': [{
            'reading_id': 'r-valid-1',
            'taken_at': '2026-06-01T12:00:00Z',
            'value': 30.0, # changed value
            'unit': 'C'
        }]
    }
    client.put('/gw/v1/batches/b-conflict', batch_conflict, HTTP_AUTHORIZATION=auth_header, format='json')
    b = client.get('/api/v1/batches/b-conflict').data
    assert b['processing'] == 'quarantined'


@pytest.mark.django_db
def test_variation_2_late_reading_correction(client):
    """
    Variation 2 from PDF page 9:
    - On day 15, S went dormant because days 1-14 were quiet days.
    - On day 20, G uploads a batch holding a reading taken on day 9.
    - After recompute, days 10-19 are 10 quiet days, so S should be active.
    - A correction entry records dormant -> active, and the day-15 dormant entry stays.
    """
    res_gw = client.post('/api/v1/gateways', {'gateway_id': 'G2', 'name': 'Gateway G2'}, format='json')
    token = res_gw.data['token']
    auth_header = f'Bearer {token}'

    client.post('/api/v1/sensors', {'sensor_id': 'S2', 'type': 'temperature'}, format='json')
    client.put('/api/v1/sensors/S2/coverage', {'gateway_ids': ['G2']}, format='json')

    base_date = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc) # Day 0

    # Day 0: reading -> active
    client.post('/test/clock', {'now': base_date.isoformat()}, format='json')
    client.put('/gw/v1/batches/b-init-2', {
        'sensor_id': 'S2',
        'readings': [{'reading_id': 'r-d0-2', 'taken_at': base_date.isoformat(), 'value': 21.0, 'unit': 'C'}]
    }, HTTP_AUTHORIZATION=auth_header, format='json')

    # Days 1 to 14: no_readings each day
    for day in range(1, 15):
        cycle_time = base_date + timedelta(days=day)
        client.post('/test/clock', {'now': cycle_time.isoformat()}, format='json')
        client.post('/gw/v1/cycles', {
            'cycle_id': f'c2-d{day}',
            'started_at': (cycle_time - timedelta(minutes=5)).isoformat(),
            'finished_at': cycle_time.isoformat(),
            'session': 'ok',
            'results': [{'sensor_id': 'S2', 'outcome': 'no_readings'}]
        }, HTTP_AUTHORIZATION=auth_header, format='json')

    # Day 15, 00:00: S goes dormant
    day15_time = datetime(2026, 3, 16, 0, 0, 0, tzinfo=timezone.utc)
    client.post('/test/clock', {'now': day15_time.isoformat()}, format='json')
    s = client.get('/api/v1/sensors/S2').data
    assert s['lifecycle'] == 'dormant'

    # Days 15 to 19: cycles each day
    for day in range(15, 20):
        cycle_time = base_date + timedelta(days=day)
        client.post('/test/clock', {'now': cycle_time.isoformat()}, format='json')
        client.post('/gw/v1/cycles', {
            'cycle_id': f'c2-d{day}',
            'started_at': (cycle_time - timedelta(minutes=5)).isoformat(),
            'finished_at': cycle_time.isoformat(),
            'session': 'ok',
            'results': [{'sensor_id': 'S2', 'outcome': 'no_readings'}]
        }, HTTP_AUTHORIZATION=auth_header, format='json')

    # Advance clock to Day 20, 12:00
    day20_time = base_date + timedelta(days=20)
    client.post('/test/clock', {'now': day20_time.isoformat()}, format='json')

    # On Day 20, G uploads batch with reading taken on Day 9 (March 10)
    day9_time = base_date + timedelta(days=9)
    batch_late = {
        'sensor_id': 'S2',
        'readings': [{
            'reading_id': 'r-late-d9',
            'taken_at': day9_time.isoformat(),
            'value': 22.0,
            'unit': 'C'
        }]
    }
    client.put('/gw/v1/batches/b-late', batch_late, HTTP_AUTHORIZATION=auth_header, format='json')

    # Verify S2 state is now ACTIVE
    s = client.get('/api/v1/sensors/S2').data
    assert s['lifecycle'] == 'active'

    # Check timeline for correction entry and dormant entry
    timeline = client.get('/api/v1/sensors/S2/timeline').data
    correction_entries = [e for e in timeline if e['kind'] == 'correction']
    assert len(correction_entries) == 1
    assert correction_entries[0]['from'] == 'dormant'
    assert correction_entries[0]['to'] == 'active'
    assert 'b-late' in correction_entries[0]['evidence_ids']

    # Dormant entry on day 15 still exists in timeline!
    dormant_entries = [e for e in timeline if e.get('to') == 'dormant']
    assert len(dormant_entries) >= 1


