from rest_framework.views import exception_handler
from rest_framework.exceptions import ValidationError, NotFound, AuthenticationFailed, PermissionDenied
from rest_framework.response import Response
from rest_framework import status

class APIConflict(Exception):
    def __init__(self, code="conflict", detail="Action resulted in a conflict"):
        self.code = code
        self.detail = detail
        super().__init__(detail)

class APIValidationError(Exception):
    def __init__(self, code="invalid_body", detail="Invalid body"):
        self.code = code
        self.detail = detail
        super().__init__(detail)

def custom_exception_handler(exc, context):
    # Handle custom domain exceptions first
    if isinstance(exc, APIConflict):
        return Response({"error": exc.code, "detail": str(exc.detail)}, status=status.HTTP_409_CONFLICT)

    if isinstance(exc, APIValidationError):
        return Response({"error": exc.code, "detail": str(exc.detail)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)

    # Standard DRF exception handler
    response = exception_handler(exc, context)

    if response is not None:
        error_code = "error"
        detail_msg = ""

        if isinstance(exc, ValidationError):
            response.status_code = status.HTTP_422_UNPROCESSABLE_ENTITY
            error_code = "validation_error"
            if isinstance(response.data, dict):
                first_key = next(iter(response.data))
                first_val = response.data[first_key]
                if isinstance(first_val, list):
                    detail_msg = f"{first_key}: {first_val[0]}"
                else:
                    detail_msg = f"{first_key}: {first_val}"
            elif isinstance(response.data, list):
                detail_msg = str(response.data[0])
            else:
                detail_msg = str(response.data)

        elif isinstance(exc, NotFound):
            error_code = "not_found"
            detail_msg = str(response.data.get("detail", "Resource not found"))

        elif isinstance(exc, AuthenticationFailed):
            error_code = "unauthorized"
            detail_msg = str(response.data.get("detail", "Authentication failed"))

        elif isinstance(exc, PermissionDenied):
            error_code = "forbidden"
            detail_msg = str(response.data.get("detail", "Permission denied"))

        else:
            if isinstance(response.data, dict) and "detail" in response.data:
                detail_msg = str(response.data["detail"])
            else:
                detail_msg = str(response.data)

        response.data = {
            "error": error_code,
            "detail": detail_msg
        }

    return response
