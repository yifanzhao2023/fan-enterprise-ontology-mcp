from typing import Any


class FeomcpError(Exception):
    code = "BACKEND_UNAVAILABLE"

    def __init__(
        self,
        message: str,
        *,
        hint: str = "",
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.retryable = retryable
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "hint": self.hint,
            "retryable": self.retryable,
            "details": self.details,
        }


class SchemaNotFound(FeomcpError):
    code = "SCHEMA_NOT_FOUND"


class QuerySyntaxError(FeomcpError):
    code = "QUERY_SYNTAX"


class QueryRejected(FeomcpError):
    code = "QUERY_REJECTED"


class QueryLimitExceeded(FeomcpError):
    code = "QUERY_LIMIT"


class ValidationFailed(FeomcpError):
    code = "VALIDATION_FAILED"


class WriteRejected(FeomcpError):
    code = "WRITE_REJECTED"


class BackendUnavailable(FeomcpError):
    code = "BACKEND_UNAVAILABLE"
