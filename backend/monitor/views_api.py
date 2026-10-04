from datetime import timedelta
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import NotFound

from .models import (
    Gateway, Sensor, GatewaySensorAssignment, Command,
    Batch, TimelineEntry, CycleReport
)
from .serializers import GatewaySerializer, SensorSerializer, TimelineEntrySerializer, BatchSerializer
from .clock import get_now, format_iso
from .exceptions import APIConflict, APIValidationError
from .engine import (
    record_timeline, compute_sensor_coverage, recompute_coverage_for_sensor
)


class GatewayListCreateView(APIView):
    def get(self, request):
        gateways = Gateway.objects.all().order_by('gateway_id')
        serializer = GatewaySerializer(gateways, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        data = request.data
        gateway_id = data.get('gateway_id')
        name = data.get('name')

        if not gateway_id or not name:
            raise APIValidationError(detail="gateway_id and name are required")

        if Gateway.objects.filter(gateway_id=gateway_id).exists():
            raise APIConflict(code="duplicate_gateway", detail="Gateway ID already exists")

        now = get_now()
        gw = Gateway.objects.create(
            gateway_id=gateway_id,
            name=name,
            status='new',
            status_since=now,
            command_state='running'
        )

        record_timeline(
            entity=gw,
            kind='action',
            axis='status',
            from_state=None,
            to_state='new',
            effective_at=now,
            rule='gateway_created'
        )

        return Response({"token": gw.token}, status=status.HTTP_201_CREATED)


class GatewayDetailView(APIView):
    def get(self, request, id):
        gw = Gateway.objects.filter(gateway_id=id).first()
        if not gw:
            raise NotFound(detail="Gateway not found")
        serializer = GatewaySerializer(gw)
        return Response(serializer.data, status=status.HTTP_200_OK)


class GatewayActionsView(APIView):
    def post(self, request, id):
        gw = Gateway.objects.filter(gateway_id=id).first()
        if not gw:
            raise NotFound(detail="Gateway not found")

        data = request.data
        action = data.get('action')
        reason = data.get('reason')
        now = get_now()

        if action == 'mark_spare':
            if gw.status == 'retired':
                raise APIConflict(code="gateway_retired", detail="Retired gateway cannot be marked spare")
            # Allowed only when the gateway covers no sensors; otherwise 409
            has_sensors = GatewaySensorAssignment.objects.filter(gateway=gw).exists()
            if has_sensors:
                raise APIConflict(code="gateway_covers_sensors", detail="Gateway covers sensors; cannot mark spare")
            old_status = gw.status
            gw.status = 'spare'
            gw.status_since = now
            gw.save()
            record_timeline(gw, 'action', 'status', old_status, 'spare', now, 'operator_mark_spare')

        elif action == 'suspend':
            if not reason:
                raise APIValidationError(detail="reason required for suspend")
            if gw.status == 'retired':
                raise APIConflict(code="gateway_retired", detail="Cannot suspend retired gateway")
            old_status = gw.status
            gw.status = 'suspended'
            gw.status_since = now
            gw.suspend_reason = reason
            gw.save()
            record_timeline(gw, 'action', 'status', old_status, 'suspended', now, 'operator_suspend')

            # Recompute coverage on all covered sensors
            for assignment in gw.sensor_assignments.all():
                recompute_coverage_for_sensor(assignment.sensor, now)

        elif action == 'unsuspend':
            if gw.status != 'suspended':
                raise APIConflict(code="not_suspended", detail="Gateway is not suspended")
            old_status = gw.status

            # Status is derived from evidence:
            # disconnected if latest auth failure is newer than last_qualifying_at;
            # otherwise connected if last_qualifying_at is within 12 hours;
            # otherwise stale; new if there has never been qualifying evidence
            latest_auth_failure = gw.disconnected_since
            last_qual = gw.last_qualifying_at

            if latest_auth_failure and (not last_qual or latest_auth_failure > last_qual):
                derived_status = 'disconnected'
            elif last_qual:
                if (now - last_qual) <= timedelta(hours=12):
                    derived_status = 'connected'
                else:
                    derived_status = 'stale'
            else:
                derived_status = 'new'

            gw.status = derived_status
            gw.status_since = now
            gw.suspend_reason = None
            gw.save()
            record_timeline(gw, 'action', 'status', old_status, derived_status, now, 'operator_unsuspend')

            # Recompute coverage on all covered sensors
            for assignment in gw.sensor_assignments.all():
                recompute_coverage_for_sensor(assignment.sensor, now)

        elif action == 'retire':
            old_status = gw.status
            gw.status = 'retired'
            gw.status_since = now
            gw.save()
            record_timeline(gw, 'action', 'status', old_status, 'retired', now, 'operator_retire')

            # Recompute coverage on all covered sensors
            for assignment in gw.sensor_assignments.all():
                recompute_coverage_for_sensor(assignment.sensor, now)

        elif action == 'stop':
            # Stop while state is stop_pending or stopped -> 409
            if gw.command_state in ['stop_pending', 'stopped']:
                raise APIConflict(code="already_stopping", detail=f"Cannot stop while in {gw.command_state}")
            
            gw.command_seq += 1
            cmd = Command.objects.create(
                gateway=gw,
                seq=gw.command_seq,
                type='stop',
                issued_at=now
            )
            old_state = gw.command_state
            gw.command_state = 'stop_pending'
            gw.save()
            record_timeline(gw, 'action', 'command_state', old_state, 'stop_pending', now, 'operator_stop', [cmd.command_id])

        elif action == 'resume':
            # Resume while state is running -> 409
            if gw.command_state == 'running':
                raise APIConflict(code="already_running", detail="Cannot resume while running")
            
            gw.command_seq += 1
            cmd = Command.objects.create(
                gateway=gw,
                seq=gw.command_seq,
                type='resume',
                issued_at=now
            )
            old_state = gw.command_state
            gw.command_state = 'resume_pending'
            gw.save()
            record_timeline(gw, 'action', 'command_state', old_state, 'resume_pending', now, 'operator_resume', [cmd.command_id])

        else:
            raise APIValidationError(detail=f"Unknown action: {action}")

        serializer = GatewaySerializer(gw)
        return Response(serializer.data, status=status.HTTP_200_OK)


class SensorListCreateView(APIView):
    def get(self, request):
        sensors = Sensor.objects.all().order_by('sensor_id')
        serializer = SensorSerializer(sensors, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        data = request.data
        sensor_id = data.get('sensor_id')
        sensor_type = data.get('type')

        if not sensor_id or not sensor_type:
            raise APIValidationError(detail="sensor_id and type required")

        if Sensor.objects.filter(sensor_id=sensor_id).exists():
            raise APIConflict(code="duplicate_sensor", detail="Sensor ID already exists")

        now = get_now()
        sensor = Sensor.objects.create(
            sensor_id=sensor_id,
            type=sensor_type,
            lifecycle='pending',
            lifecycle_since=now
        )

        record_timeline(
            entity=sensor,
            kind='action',
            axis='lifecycle',
            from_state=None,
            to_state='pending',
            effective_at=now,
            rule='sensor_created'
        )

        serializer = SensorSerializer(sensor)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class SensorDetailView(APIView):
    def get(self, request, id):
        sensor = Sensor.objects.filter(sensor_id=id).first()
        if not sensor:
            raise NotFound(detail="Sensor not found")
        serializer = SensorSerializer(sensor)
        return Response(serializer.data, status=status.HTTP_200_OK)


class SensorCoverageView(APIView):
    def put(self, request, id):
        sensor = Sensor.objects.filter(sensor_id=id).first()
        if not sensor:
            raise NotFound(detail="Sensor not found")

        gateway_ids = request.data.get('gateway_ids', [])
        now = get_now()

        # Check all gateways exist, and none is retired
        gateways = []
        for gid in gateway_ids:
            gw = Gateway.objects.filter(gateway_id=gid).first()
            if not gw:
                raise NotFound(detail=f"Gateway {gid} not found")
            if gw.status == 'retired':
                raise APIConflict(code="gateway_retired", detail=f"Gateway {gid} is retired")
            gateways.append(gw)

        # Clear existing assignments
        GatewaySensorAssignment.objects.filter(sensor=sensor).delete()

        # Assign new gateways
        for gw in gateways:
            GatewaySensorAssignment.objects.create(
                gateway=gw,
                sensor=sensor,
                assigned_at=now
            )
            # If a gateway was spare, assigning a sensor to it makes it new!
            if gw.status == 'spare':
                gw.status = 'new'
                gw.status_since = now
                gw.save()
                record_timeline(gw, 'transition', 'status', 'spare', 'new', now, 'sensor_assigned')

        # Recompute coverage and check sensor transitions
        recompute_coverage_for_sensor(sensor, now)

        serializer = SensorSerializer(sensor)
        return Response(serializer.data, status=status.HTTP_200_OK)


class SensorActionsView(APIView):
    def post(self, request, id):
        sensor = Sensor.objects.filter(sensor_id=id).first()
        if not sensor:
            raise NotFound(detail="Sensor not found")

        data = request.data
        action = data.get('action')
        reason = data.get('reason')
        now = get_now()

        if sensor.lifecycle == 'decommissioned':
            raise APIConflict(code="already_decommissioned", detail="Sensor is already decommissioned")

        if action == 'decommission':
            if not reason:
                raise APIValidationError(detail="reason required for decommission")

            old_lifecycle = sensor.lifecycle
            sensor.lifecycle = 'decommissioned'
            sensor.lifecycle_reason = reason
            sensor.lifecycle_since = now
            sensor.next_evaluation_at = None
            sensor.save()

            record_timeline(
                entity=sensor,
                kind='action',
                axis='lifecycle',
                from_state=old_lifecycle,
                to_state='decommissioned',
                effective_at=now,
                rule='operator_decommission'
            )
        else:
            raise APIValidationError(detail=f"Unknown action: {action}")

        serializer = SensorSerializer(sensor)
        return Response(serializer.data, status=status.HTTP_200_OK)


class GatewayTimelineView(APIView):
    def get(self, request, id):
        gw = Gateway.objects.filter(gateway_id=id).first()
        if not gw:
            raise NotFound(detail="Gateway not found")
        entries = gw.timeline_entries.all().order_by('seq')
        serializer = TimelineEntrySerializer(entries, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class SensorTimelineView(APIView):
    def get(self, request, id):
        sensor = Sensor.objects.filter(sensor_id=id).first()
        if not sensor:
            raise NotFound(detail="Sensor not found")
        entries = sensor.timeline_entries.all().order_by('seq')
        serializer = TimelineEntrySerializer(entries, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class BatchDetailView(APIView):
    def get(self, request, id):
        batch = Batch.objects.filter(batch_id=id).first()
        if not batch:
            raise NotFound(detail="Batch not found")
        serializer = BatchSerializer(batch)
        return Response(serializer.data, status=status.HTTP_200_OK)
