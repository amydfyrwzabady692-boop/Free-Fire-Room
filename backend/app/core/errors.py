from __future__ import annotations

from fastapi import HTTPException, status


class AppError(Exception):
    def __init__(self, code: str, message: str, http_status: int = 400, details: dict | None = None):
        self.code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}
        super().__init__(message)

    def to_http(self) -> HTTPException:
        return HTTPException(
            status_code=self.http_status,
            detail={"code": self.code, "message": self.message, "details": self.details},
        )


class NotFoundError(AppError):
    def __init__(self, code: str = "not_found", message: str = "یافت نشد"):
        super().__init__(code, message, status.HTTP_404_NOT_FOUND)


class ForbiddenError(AppError):
    def __init__(self, code: str = "forbidden", message: str = "دسترسی مجاز نیست"):
        super().__init__(code, message, status.HTTP_403_FORBIDDEN)


class UnauthorizedError(AppError):
    def __init__(self, code: str = "unauthorized", message: str = "احراز هویت لازم است"):
        super().__init__(code, message, status.HTTP_401_UNAUTHORIZED)


class ConflictError(AppError):
    def __init__(self, code: str = "conflict", message: str = "تداخل داده"):
        super().__init__(code, message, status.HTTP_409_CONFLICT)


class RateLimitError(AppError):
    def __init__(
        self,
        code: str = "rate_limited",
        message: str = "تعداد درخواست بیش از حد مجاز است",
        details: dict | None = None,
    ):
        super().__init__(code, message, status.HTTP_429_TOO_MANY_REQUESTS, details)


class ValidationAppError(AppError):
    def __init__(self, code: str = "validation_error", message: str = "داده نامعتبر است", details: dict | None = None):
        super().__init__(code, message, status.HTTP_422_UNPROCESSABLE_ENTITY, details)
