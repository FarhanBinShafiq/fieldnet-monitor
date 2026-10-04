import json
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied, NotFound

from .models import Gateway, Sensor, GatewaySensorAssignment, CycleReport, CycleResult, Batch, Command
from .clock import get_now, parse_iso, format_iso
from .exceptions import APIConflict, APIValidationError
from .engine import (
    record_timeline, on_qualifying_evidence,
    evaluate_gateway_transitions, recompute_coverage_for_sensor
)


def authenticate_gateway(request) -> Gateway:
    auth_header = request.headers.get('Authorization', '')
    if not auth_header.startswith('Bearer '):
        raise AuthenticationFailed(detail="Missing or invalid Authorization header")
    token = auth_header.split(' ', 1)[1].strip()
    gw = Gateway.objects.filter(token=token).first()
    if not gw:
        raise AuthenticationFailed(detail="Unknown gateway token")
    if gw.status == 'retired':
        raise PermissionDenied(detail="Gateway is retired")
    return gw


class GatewayHeartbeatView(APIView):
    def post(self, request):
        gw = authenticate_gateway(request)
        data = request.data
        sent_at_str = data.get('sent_at')
        session = data.get('session')

        if not sent_at_str or session not in ['ok', 'auth_failed']:
            raise APIValidationError(detail="sent_at and session ('ok'|'auth_failed') required")

        sent_at = parse_iso(sent_at_str)
        now = get_now()

        gw.last_heartbeat_at = now

        if session == 'auth_failed':
            if gw.status in ['new', 'connected', 'stale']:
                old_status = gw.status
                gw.status = 'disconnected'
                if gw.disconnected_since is None:
                    gw.disconnected_since = sent_at
                gw.status_since = sent_at
                gw.save()
                record_timeline(
                    entity=gw,
                    kind='transition',
                    axis='status',
                    from_state=old_status,
                    to_state='disconnected',
                    effective_at=sent_at,
                    rule='heartbeat_auth_failed'
                )
        gw.save()
        return Response(status=status.HTTP_204_NO_CONTENT)


class GatewayCyclesView(APIView):
    def post(self, request):
        gw = authenticate_gateway(request)
        data = request.data
        cycle_id = data.get('cycle_id')
        started_at_str = data.get('started_at')
        finished_at_str = data.get('finished_at')
        session = data.get('session')
        results = data.get('results', [])

        if not cycle_id or not started_at_str or not finished_at_str or session not in ['ok', 'auth_failed']:
            raise APIValidationError(detail="Missing cycle_id, started_at, finished_at, or valid session")

        started_at = parse_iso(started_at_str)
        finished_at = parse_iso(finished_at_str)
        now = get_now()

        # Idempotency check
        existing = CycleReport.objects.filter(cycle_id=cycle_id).first()
        if existing:
            if existing.raw_payload == data:
                return Response({"ignored": []}, status=status.HTTP_200_OK)
            else:
                raise APIConflict(code="cycle_conflict", detail="Cycle ID already exists with different body")

        # Check outcome and batch_id rules
        for r in results:
            outcome = r.get('outcome')
            batch_id = r.get('batch_id')
            if outcome not in ['readings', 'no_readings', 'could_not_read', 'timeout']:
                raise APIValidationError(detail=f"Invalid outcome: {outcome}")
            if outcome == 'readings' and not batch_id:
                raise APIValidationError(detail="batch_id is required when outcome is readings")
            if outcome != 'readings' and batch_id:
                raise APIValidationError(detail="batch_id must be absent when outcome is not readings")

        # Covered sensors
        covered_ids = set(
            GatewaySensorAssignment.objects.filter(gateway=gw).values_list('sensor_id', flat=True)
        )
        ignored = []

        report = CycleReport.objects.create(
            cycle_id=cycle_id,
            gateway=gw,
            started_at=started_at,
            finished_at=finished_at,
            session=session,
            received_at=now,
            raw_payload=data
        )

        has_qualifying = False

        for r in results:
            sensor_id = r.get('sensor_id')
            outcome = r.get('outcome')
            batch_id = r.get('batch_id')

            if sensor_id not in covered_ids:
                ignored.append(sensor_id)
                continue

            if session == 'auth_failed':
                outcome = 'could_not_read'

            CycleResult.objects.create(
                cycle=report,
                sensor_id=sensor_id,
                outcome=outcome,
                batch_id=batch_id
            )

            if session == 'ok' and outcome in ['readings', 'no_readings']:
                has_qualifying = True

        # Process gateway status update from cycle
        if session == 'auth_failed':
            if gw.status in ['new', 'connected', 'stale']:
                old_status = gw.status
                gw.status = 'disconnected'
                if gw.disconnected_since is None:
                    gw.disconnected_since = finished_at
                gw.status_since = finished_at
                gw.save()
                record_timeline(
                    entity=gw,
                    kind='transition',
                    axis='status',
                    from_state=old_status,
                    to_state='disconnected',
                    effective_at=finished_at,
                    rule='cycle_auth_failed',
                    evidence_ids=[cycle_id]
                )
        elif has_qualifying:
            on_qualifying_evidence(gw, finished_at, cycle_id)

        return Response({"ignored": ignored}, status=status.HTTP_202_ACCEPTED)


class GatewayBatchView(APIView):
    def put(self, request, batch_id):
        gw = authenticate_gateway(request)
        data = request.data
        sensor_id = data.get('sensor_id')
        readings = data.get('readings')

        if not sensor_id or readings is None or not isinstance(readings, list):
            raise APIValidationError(detail="sensor_id and readings list required")

        now = get_now()

        # Idempotency and uniqueness across gateways
        existing = Batch.objects.filter(batch_id=batch_id).first()
        if existing:
            if existing.gateway_id != gw.gateway_id:
                raise APIConflict(code="batch_conflict", detail="Batch ID already claimed by another gateway")
            if existing.raw_payload == data:
                return Response(status=status.HTTP_200_OK)
            else:
                raise APIConflict(code="batch_conflict", detail="Batch ID exists with different body")

        batch = Batch.objects.create(
            batch_id=batch_id,
            gateway=gw,
            sensor_id_raw=sensor_id,
            received_at=now,
            raw_payload=data
        )

        from .engine import process_batch
        # Background / immediate ingestion
        process_batch(batch)

        return Response(status=status.HTTP_202_ACCEPTED)


class GatewayCommandsView(APIView):
    def get(self, request):
        gw = authenticate_gateway(request)
        latest_cmd = gw.commands.filter(acknowledged=False).order_by('-seq').first()
        if latest_cmd:
            return Response({
                "commands": [{
                    "command_id": latest_cmd.command_id,
                    "seq": latest_cmd.seq,
                    "type": latest_cmd.type,
                    "issued_at": format_iso(latest_cmd.issued_at)
                }]
            }, status=status.HTTP_200_OK)
        return Response({"commands": []}, status=status.HTTP_200_OK)


class GatewayCommandAckView(APIView):
    def post(self, request, command_id):
        gw = authenticate_gateway(request)
        data = request.data
        acked_at_str = data.get('acked_at')
        if not acked_at_str:
            raise APIValidationError(detail="acked_at required")

        acked_at = parse_iso(acked_at_str)
        now = get_now()

        cmd = Command.objects.filter(command_id=command_id).first()
        if not cmd or cmd.gateway_id != gw.gateway_id:
            raise NotFound(detail="Command does not belong to this gateway")

        if cmd.acknowledged:
            # Idempotent response
            return Response(status=status.HTTP_204_NO_CONTENT)

        cmd.acknowledged = True
        cmd.acked_at = acked_at
        cmd.save()

        record_timeline(
            entity=gw,
            kind='ack',
            axis='command_state',
            from_state=gw.command_state,
            to_state=gw.command_state,
            effective_at=acked_at,
            evidence_ids=[cmd.command_id]
        )

        # Check if latest command
        latest_cmd = gw.commands.order_by('-seq').first()
        if latest_cmd and latest_cmd.command_id == cmd.command_id:
            old_cs = gw.command_state
            if cmd.type == 'stop':
                gw.command_state = 'stopped'
                gw.stop_period_acked_at = acked_at
                gw.save()
                record_timeline(gw, 'transition', 'command_state', old_cs, 'stopped', acked_at, 'command_ack', [cmd.command_id])
            elif cmd.type == 'resume':
                gw.command_state = 'running'
                gw.resume_period_acked_at = acked_at
                gw.has_collecting_after_stop = False
                gw.save()
                record_timeline(gw, 'transition', 'command_state', old_cs, 'running', acked_at, 'command_ack', [cmd.command_id])

        return Response(status=status.HTTP_204_NO_CONTENT)
