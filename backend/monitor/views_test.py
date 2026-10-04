from django.conf import settings
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.exceptions import NotFound

from .models import (
    SystemState, Gateway, Sensor, GatewaySensorAssignment,
    Command, Batch, Reading, CycleReport, CycleResult, TimelineEntry
)
from .clock import set_now, get_now, parse_iso, reset_clock
from .engine import advance_time_and_drain
from .exceptions import APIConflict, APIValidationError


def ensure_test_mode():
    if not getattr(settings, 'TEST_MODE', False):
        raise NotFound(detail="Test endpoints only available when TEST_MODE=1")


class TestResetView(APIView):
    def post(self, request):
        ensure_test_mode()
        # Clear all application tables in dependency order
        TimelineEntry.objects.all().delete()
        Reading.objects.all().delete()
        CycleResult.objects.all().delete()
        CycleReport.objects.all().delete()
        Batch.objects.all().delete()
        Command.objects.all().delete()
        GatewaySensorAssignment.objects.all().delete()
        Sensor.objects.all().delete()
        Gateway.objects.all().delete()
        reset_clock()
        return Response(status=status.HTTP_200_OK)


class TestClockView(APIView):
    def post(self, request):
        ensure_test_mode()
        now_str = request.data.get('now')
        if not now_str:
            raise APIValidationError(detail="now timestamp required")

        target_time = parse_iso(now_str)
        # Advance virtual clock
        set_now(target_time)

        # Before it returns, process every received batch and retry due up to now,
        # and apply every due transition in event-time order
        advance_time_and_drain(target_time)

        return Response(status=status.HTTP_200_OK)


class TestDrainView(APIView):
    def post(self, request):
        ensure_test_mode()
        now = get_now()
        advance_time_and_drain(now)
        return Response(status=status.HTTP_200_OK)


class TestFaultsView(APIView):
    def post(self, request):
        ensure_test_mode()
        failures = request.data.get('processing_failures', 0)
        state = SystemState.get_state()
        state.processing_failures_remaining = int(failures)
        state.save()
        return Response(status=status.HTTP_200_OK)
