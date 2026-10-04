from django.urls import path
from .views_gw import (
    GatewayHeartbeatView, GatewayCyclesView, GatewayBatchView,
    GatewayCommandsView, GatewayCommandAckView
)
from .views_api import (
    GatewayListCreateView, GatewayDetailView, GatewayActionsView, GatewayTimelineView,
    SensorListCreateView, SensorDetailView, SensorCoverageView, SensorActionsView,
    SensorTimelineView, BatchDetailView
)
from .views_test import (
    TestResetView, TestClockView, TestDrainView, TestFaultsView
)

urlpatterns = [
    # Gateway API
    path('gw/v1/heartbeat', GatewayHeartbeatView.as_view(), name='gw-heartbeat'),
    path('gw/v1/cycles', GatewayCyclesView.as_view(), name='gw-cycles'),
    path('gw/v1/batches/<str:batch_id>', GatewayBatchView.as_view(), name='gw-batch'),
    path('gw/v1/commands', GatewayCommandsView.as_view(), name='gw-commands'),
    path('gw/v1/commands/<str:command_id>/ack', GatewayCommandAckView.as_view(), name='gw-command-ack'),

    # Operator and State API
    path('api/v1/gateways', GatewayListCreateView.as_view(), name='api-gateways'),
    path('api/v1/gateways/<str:id>', GatewayDetailView.as_view(), name='api-gateway-detail'),
    path('api/v1/gateways/<str:id>/actions', GatewayActionsView.as_view(), name='api-gateway-actions'),
    path('api/v1/gateways/<str:id>/timeline', GatewayTimelineView.as_view(), name='api-gateway-timeline'),

    path('api/v1/sensors', SensorListCreateView.as_view(), name='api-sensors'),
    path('api/v1/sensors/<str:id>', SensorDetailView.as_view(), name='api-sensor-detail'),
    path('api/v1/sensors/<str:id>/coverage', SensorCoverageView.as_view(), name='api-sensor-coverage'),
    path('api/v1/sensors/<str:id>/actions', SensorActionsView.as_view(), name='api-sensor-actions'),
    path('api/v1/sensors/<str:id>/timeline', SensorTimelineView.as_view(), name='api-sensor-timeline'),

    path('api/v1/batches/<str:id>', BatchDetailView.as_view(), name='api-batch-detail'),

    # Test Endpoints
    path('test/reset', TestResetView.as_view(), name='test-reset'),
    path('test/clock', TestClockView.as_view(), name='test-clock'),
    path('test/drain', TestDrainView.as_view(), name='test-drain'),
    path('test/faults', TestFaultsView.as_view(), name='test-faults'),
]
