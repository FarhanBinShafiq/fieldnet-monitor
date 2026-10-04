import uuid
from django.db import models
from django.utils import timezone

class SystemState(models.Model):
    """
    Holds global simulation state such as the current virtual clock in TEST_MODE
    and remaining fault counts for testing retries.
    """
    singleton_id = models.IntegerField(primary_key=True, default=1)
    virtual_clock = models.DateTimeField(null=True, blank=True)
    processing_failures_remaining = models.IntegerField(default=0)

    class Meta:
        db_table = 'system_state'

    @classmethod
    def get_state(cls):
        obj, _ = cls.objects.get_or_create(singleton_id=1)
        return obj


class Gateway(models.Model):
    STATUS_CHOICES = [
        ('new', 'new'),
        ('spare', 'spare'),
        ('connected', 'connected'),
        ('stale', 'stale'),
        ('disconnected', 'disconnected'),
        ('suspended', 'suspended'),
        ('retired', 'retired'),
    ]

    COMMAND_STATE_CHOICES = [
        ('running', 'running'),
        ('stop_pending', 'stop_pending'),
        ('stopped', 'stopped'),
        ('stop_failed', 'stop_failed'),
        ('resume_pending', 'resume_pending'),
        ('resume_failed', 'resume_failed'),
    ]

    gateway_id = models.CharField(max_length=128, primary_key=True)
    name = models.CharField(max_length=256)
    token = models.CharField(max_length=128, unique=True, default=uuid.uuid4)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default='new')
    status_since = models.DateTimeField()
    command_state = models.CharField(max_length=32, choices=COMMAND_STATE_CHOICES, default='running')
    
    last_heartbeat_at = models.DateTimeField(null=True, blank=True)
    last_qualifying_at = models.DateTimeField(null=True, blank=True)
    disconnected_since = models.DateTimeField(null=True, blank=True)
    
    # Internal tracking for commands and stop periods
    command_seq = models.IntegerField(default=0)
    stop_period_acked_at = models.DateTimeField(null=True, blank=True) # acked_at of active stop
    resume_period_acked_at = models.DateTimeField(null=True, blank=True)
    has_collecting_after_stop = models.BooleanField(default=False)
    
    # Operator suspension reason
    suspend_reason = models.TextField(null=True, blank=True)

    class Meta:
        db_table = 'gateways'

    @property
    def coverage_class(self):
        if self.status == 'connected':
            if self.command_state in ['running', 'stop_pending', 'stop_failed']:
                return 'available'
            elif self.command_state in ['stopped', 'resume_pending', 'resume_failed']:
                return 'stopped'
        elif self.status in ['new', 'stale', 'disconnected']:
            return 'recoverable'
        elif self.status in ['suspended', 'retired']:
            return 'dead'
        return 'recoverable'

    @property
    def flags(self):
        if self.has_collecting_after_stop:
            return ['collecting_after_stop']
        return []


class Sensor(models.Model):
    LIFECYCLE_CHOICES = [
        ('pending', 'pending'),
        ('active', 'active'),
        ('dormant', 'dormant'),
        ('sampling', 'sampling'),
        ('retired', 'retired'),
        ('decommissioned', 'decommissioned'),
    ]

    sensor_id = models.CharField(max_length=128, primary_key=True)
    type = models.CharField(max_length=64) # temperature, rain, wind
    lifecycle = models.CharField(max_length=32, choices=LIFECYCLE_CHOICES, default='pending')
    lifecycle_reason = models.CharField(max_length=256, null=True, blank=True)
    lifecycle_since = models.DateTimeField()

    # Counters
    quiet_checked_days = models.IntegerField(default=0)
    sampling_cycles_done = models.IntegerField(default=0)
    
    # State tracking for available time
    wait_window_started_at = models.DateTimeField(null=True, blank=True)
    available_seconds_accumulated = models.FloatField(default=0.0)
    last_coverage_checked_at = models.DateTimeField(null=True, blank=True)
    last_reading_taken_at = models.DateTimeField(null=True, blank=True)
    next_evaluation_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = 'sensors'


class GatewaySensorAssignment(models.Model):
    gateway = models.ForeignKey(Gateway, on_delete=models.CASCADE, related_name='sensor_assignments')
    sensor = models.ForeignKey(Sensor, on_delete=models.CASCADE, related_name='gateway_assignments')
    assigned_at = models.DateTimeField()

    class Meta:
        db_table = 'gateway_sensor_assignments'
        unique_together = ('gateway', 'sensor')


class Command(models.Model):
    TYPE_CHOICES = [
        ('stop', 'stop'),
        ('resume', 'resume'),
    ]

    command_id = models.CharField(max_length=128, primary_key=True, default=uuid.uuid4)
    gateway = models.ForeignKey(Gateway, on_delete=models.CASCADE, related_name='commands')
    seq = models.IntegerField()
    type = models.CharField(max_length=16, choices=TYPE_CHOICES)
    issued_at = models.DateTimeField()
    acked_at = models.DateTimeField(null=True, blank=True)
    acknowledged = models.BooleanField(default=False)

    class Meta:
        db_table = 'commands'
        ordering = ['seq']


class Batch(models.Model):
    PROCESSING_CHOICES = [
        ('received', 'received'),
        ('processed', 'processed'),
        ('partially_processed', 'partially_processed'),
        ('retrying', 'retrying'),
        ('quarantined', 'quarantined'),
    ]

    batch_id = models.CharField(max_length=128, primary_key=True)
    gateway = models.ForeignKey(Gateway, on_delete=models.CASCADE, related_name='batches')
    sensor = models.ForeignKey(Sensor, on_delete=models.CASCADE, related_name='batches', null=True, blank=True)
    sensor_id_raw = models.CharField(max_length=128)
    
    received_at = models.DateTimeField()
    processed_at = models.DateTimeField(null=True, blank=True)
    processing = models.CharField(max_length=32, choices=PROCESSING_CHOICES, default='received')
    
    attempts = models.IntegerField(default=0)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    accepted_count = models.IntegerField(default=0)
    
    quarantined = models.JSONField(default=list) # [{reading_id, reason}]
    quarantine_reason = models.CharField(max_length=128, null=True, blank=True)
    
    raw_payload = models.JSONField()

    class Meta:
        db_table = 'batches'


class Reading(models.Model):
    reading_id = models.CharField(max_length=128, primary_key=True)
    batch = models.ForeignKey(Batch, on_delete=models.CASCADE, related_name='readings')
    gateway = models.ForeignKey(Gateway, on_delete=models.CASCADE, related_name='readings')
    sensor = models.ForeignKey(Sensor, on_delete=models.CASCADE, related_name='readings')
    taken_at = models.DateTimeField()
    value = models.FloatField()
    unit = models.CharField(max_length=32)
    accepted = models.BooleanField(default=True)
    
    created_at = models.DateTimeField()

    class Meta:
        db_table = 'readings'


class CycleReport(models.Model):
    cycle_id = models.CharField(max_length=128, primary_key=True)
    gateway = models.ForeignKey(Gateway, on_delete=models.CASCADE, related_name='cycles')
    started_at = models.DateTimeField()
    finished_at = models.DateTimeField()
    session = models.CharField(max_length=32) # ok or auth_failed
    received_at = models.DateTimeField()
    raw_payload = models.JSONField()

    class Meta:
        db_table = 'cycle_reports'


class CycleResult(models.Model):
    cycle = models.ForeignKey(CycleReport, on_delete=models.CASCADE, related_name='results')
    sensor_id = models.CharField(max_length=128)
    outcome = models.CharField(max_length=32) # readings, no_readings, could_not_read, timed_out
    batch_id = models.CharField(max_length=128, null=True, blank=True)

    class Meta:
        db_table = 'cycle_results'


class TimelineEntry(models.Model):
    KIND_CHOICES = [
        ('transition', 'transition'),
        ('correction', 'correction'),
        ('action', 'action'),
        ('ack', 'ack'),
        ('flag', 'flag'),
        ('rule_change', 'rule_change'),
    ]

    gateway = models.ForeignKey(Gateway, on_delete=models.CASCADE, related_name='timeline_entries', null=True, blank=True)
    sensor = models.ForeignKey(Sensor, on_delete=models.CASCADE, related_name='timeline_entries', null=True, blank=True)
    
    seq = models.IntegerField()
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    axis = models.CharField(max_length=32) # status, command_state, lifecycle, flag, etc.
    from_state = models.CharField(max_length=128, null=True, blank=True, db_column='from_state')
    to_state = models.CharField(max_length=128, null=True, blank=True, db_column='to_state')
    
    effective_at = models.DateTimeField()
    recorded_at = models.DateTimeField()
    rule = models.CharField(max_length=256, null=True, blank=True)
    evidence_ids = models.JSONField(default=list)

    class Meta:
        db_table = 'timeline_entries'
        ordering = ['seq']
