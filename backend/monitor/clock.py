from datetime import datetime, timezone
from django.conf import settings
from .models import SystemState

def get_now() -> datetime:
    """
    Returns the current server clock time.
    If TEST_MODE is active or virtual_clock is set in SystemState,
    it returns the virtual clock; otherwise timezone.now().
    """
    try:
        state = SystemState.get_state()
        if state.virtual_clock is not None:
            return state.virtual_clock
    except Exception:
        pass
    return datetime.now(timezone.utc)

def set_now(dt: datetime) -> datetime:
    """
    Advances the virtual clock. Moves forward only; going backwards returns 409 error.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    state = SystemState.get_state()
    if state.virtual_clock is not None and dt < state.virtual_clock:
        from .exceptions import APIConflict
        raise APIConflict(code="invalid_clock", detail="Clock cannot move backwards")
    state.virtual_clock = dt
    state.save()
    return dt

def reset_clock():
    """
    Resets the virtual clock to None.
    """
    state = SystemState.get_state()
    state.virtual_clock = None
    state.processing_failures_remaining = 0
    state.save()

def format_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    # Return ISO 8601 UTC string (ending with Z if UTC or formatted standard)
    iso = dt.isoformat()
    if iso.endswith('+00:00'):
        return iso[:-6] + 'Z'
    return iso

def parse_iso(dt_str: str) -> datetime:
    if dt_str.endswith('Z'):
        dt_str = dt_str[:-1] + '+00:00'
    dt = datetime.fromisoformat(dt_str)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
