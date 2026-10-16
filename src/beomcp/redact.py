from typing import Any, Mapping, Sequence

MASK = "***"


def redact_rows(
    rows: Sequence[Mapping[str, Any]], fields: Sequence[str]
) -> Sequence[Mapping[str, Any]]:
    """Mask named top-level keys at read time. Storage keeps complete data;
    writes are never redacted — data integrity wins."""
    if not fields:
        return list(rows)
    drop = set(fields)
    return [{k: (MASK if k in drop else v) for k, v in row.items()} for row in rows]
