import hashlib
import json
import logging
from typing import Any, Callable, Mapping

_LOG = logging.getLogger("feomcp.audit")

Sink = Callable[[Mapping[str, Any]], None]


def digest_params(params: Mapping[str, Any]) -> str:
    blob = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class AuditLogger:
    """One line per tool call. Never the result body — it can carry customer,
    contract-amount and HR data."""

    def __init__(self, sink: Sink | None = None) -> None:
        self._sink = sink or (
            lambda rec: _LOG.info("%s", json.dumps(rec, ensure_ascii=False))
        )

    def log(
        self,
        *,
        tool: str,
        params: Mapping[str, Any],
        principal: str,
        duration_ms: float,
        row_count: int,
        ok: bool,
    ) -> None:
        self._sink({
            "tool": tool,
            "params_digest": digest_params(params),
            "principal": principal,
            "duration_ms": round(duration_ms, 3),
            "row_count": row_count,
            "ok": ok,
        })
