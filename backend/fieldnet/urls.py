from django.contrib import admin
from django.urls import path, include
from django.http import JsonResponse

def api_root(request):
    return JsonResponse({
        "service": "FieldNet Monitor API",
        "status": "online",
        "console_ui": "http://localhost:3000",
        "endpoints": {
            "gateways": "/api/v1/gateways",
            "sensors": "/api/v1/sensors",
            "test_clock": "/test/clock",
            "test_reset": "/test/reset"
        }
    })

urlpatterns = [
    path('', api_root, name='api-root'),
    path('admin/', admin.site.urls),
    path('', include('monitor.urls')),
]
