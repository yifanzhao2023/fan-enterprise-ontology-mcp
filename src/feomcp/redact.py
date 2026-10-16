from typing import Any, Mapping, Sequence

MASK = "***"


def redact_rows(
    rows: Sequence[Mapping[str, Any]], fields: Sequence[str]
) -> Sequence[Mapping[str, Any]]:
    """Mask configured keys at read time, at every depth.

    Storage keeps complete data and writes are never redacted — data integrity
    wins. But a caller who sets FEOMCP_REDACT_FIELDS=hasAmount expects the value
    masked wherever it appears: as a column, inside a returned node, or in a
    nested list.
    """
    if not fields:
        return list(rows)
    drop = set(fields)
    return [_walk(row, drop) for row in rows]


def _walk(value: Any, drop: set[str]) -> Any:
    if isinstance(value, Mapping):
        return {k: (MASK if k in drop else _walk(v, drop)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_walk(v, drop) for v in value]
    return value
