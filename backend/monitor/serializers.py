from rest_framework import serializers
from .models import Gateway, Sensor, Batch, Reading, Command, TimelineEntry
from .clock import format_iso
from .engine import compute_gateway_coverage_class, compute_sensor_coverage, compute_sensor_collection

class GatewaySerializer(serializers.ModelSerializer):
    coverage_class = serializers.SerializerMethodField()
    status_since = serializers.SerializerMethodField()
    last_heartbeat_at = serializers.SerializerMethodField()
    last_qualifying_at = serializers.SerializerMethodField()
    disconnected_since = serializers.SerializerMethodField()
    flags = serializers.SerializerMethodField()

    class Meta:
        model = Gateway
        fields = [
            'gateway_id', 'name', 'status', 'status_since',
            'command_state', 'coverage_class', 'last_heartbeat_at',
            'last_qualifying_at', 'disconnected_since', 'flags'
        ]

    def get_coverage_class(self, obj):
        return obj.coverage_class

    def get_status_since(self, obj):
        return format_iso(obj.status_since)

    def get_last_heartbeat_at(self, obj):
        return format_iso(obj.last_heartbeat_at)

    def get_last_qualifying_at(self, obj):
        return format_iso(obj.last_qualifying_at)

    def get_disconnected_since(self, obj):
        return format_iso(obj.disconnected_since)

    def get_flags(self, obj):
        return obj.flags


class SensorSerializer(serializers.ModelSerializer):
    coverage = serializers.SerializerMethodField()
    collection = serializers.SerializerMethodField()
    lifecycle_since = serializers.SerializerMethodField()
    next_evaluation_at = serializers.SerializerMethodField()

    class Meta:
        model = Sensor
        fields = [
            'sensor_id', 'type', 'lifecycle', 'lifecycle_reason',
            'lifecycle_since', 'collection', 'coverage',
            'quiet_checked_days', 'sampling_cycles_done', 'next_evaluation_at'
        ]

    def get_coverage(self, obj):
        return compute_sensor_coverage(obj)

    def get_collection(self, obj):
        return compute_sensor_collection(obj)

    def get_lifecycle_since(self, obj):
        return format_iso(obj.lifecycle_since)

    def get_next_evaluation_at(self, obj):
        return format_iso(obj.next_evaluation_at)


class TimelineEntrySerializer(serializers.ModelSerializer):
    effective_at = serializers.SerializerMethodField()
    recorded_at = serializers.SerializerMethodField()

    class Meta:
        model = TimelineEntry
        fields = [
            'seq', 'kind', 'axis',
            'effective_at', 'recorded_at', 'rule', 'evidence_ids'
        ]

    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret['from'] = instance.from_state
        ret['to'] = instance.to_state
        return ret

    def get_effective_at(self, obj):
        return format_iso(obj.effective_at)

    def get_recorded_at(self, obj):
        return format_iso(obj.recorded_at)


class BatchSerializer(serializers.ModelSerializer):
    class Meta:
        model = Batch
        fields = ['processing', 'attempts', 'accepted_count', 'quarantined']
