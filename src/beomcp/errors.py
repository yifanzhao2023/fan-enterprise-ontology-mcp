from typing import Any


class BeomcpError(Exception):
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


class SchemaNotFound(BeomcpError):
    code = "SCHEMA_NOT_FOUND"


class QuerySyntaxError(BeomcpError):
    code = "QUERY_SYNTAX"


class QueryRejected(BeomcpError):
    code = "QUERY_REJECTED"


class QueryLimitExceeded(BeomcpError):
    code = "QUERY_LIMIT"


class ValidationFailed(BeomcpError):
    code = "VALIDATION_FAILED"


class WriteRejected(BeomcpError):
    code = "WRITE_REJECTED"


class BackendUnavailable(BeomcpError):
    code = "BACKEND_UNAVAILABLE"
