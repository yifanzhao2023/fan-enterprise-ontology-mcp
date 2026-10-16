import pytest

from feomcp.errors import (
    BackendUnavailable,
    QueryLimitExceeded,
    QueryRejected,
    QuerySyntaxError,
    SchemaNotFound,
    ValidationFailed,
    WriteRejected,
)


def test_payload_has_five_fields():
    e = QueryRejected(
        "CREATE is not allowed",
        hint="read-only queries only",
        retryable=False,
        details={"rule": "no-write"},
    )
    assert e.to_payload() == {
        "code": "QUERY_REJECTED",
        "message": "CREATE is not allowed",
        "hint": "read-only queries only",
        "retryable": False,
        "details": {"rule": "no-write"},
    }


def test_every_code_is_unique():
    codes = {
        SchemaNotFound.code, QuerySyntaxError.code, QueryRejected.code,
        QueryLimitExceeded.code, ValidationFailed.code, WriteRejected.code,
        BackendUnavailable.code,
    }
    assert codes == {
        "SCHEMA_NOT_FOUND", "QUERY_SYNTAX", "QUERY_REJECTED", "QUERY_LIMIT",
        "VALIDATION_FAILED", "WRITE_REJECTED", "BACKEND_UNAVAILABLE",
    }


def test_validation_failure_is_not_retryable():
    assert ValidationFailed("bad", hint="fix the field").retryable is False


def test_backend_unavailable_defaults_to_retryable_true():
    e = BackendUnavailable("down", hint="retry later", retryable=True)
    assert e.retryable is True
    assert isinstance(e, Exception)
