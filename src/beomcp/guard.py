"""Static Cypher safety checks.

Called only from QueryEngine.submit — one choke point for every caller, so no
code path can forget it. Named queries and raw Cypher go through the same guard;
templates get no back door.
"""

import re

from .errors import QueryLimitExceeded, QueryRejected

_WRITE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV)\b",
    re.IGNORECASE,
)
_FORBIDDEN_PREFIX = re.compile(r"\b(apoc|dbms)\b", re.IGNORECASE)
_CALL = re.compile(r"\bCALL\s+([A-Za-z0-9_.]+)", re.IGNORECASE)

# default deny — only these read-only introspection procedures are permitted
WHITELIST = {
    "db.labels",
    "db.propertyKeys",
    "db.relationshipTypes",
    "db.schema.relTypeNames",
    "db.schema.nodeTypeNames",
}


def check_cypher(query: str, *, max_length: int = 8192) -> None:
    text = query.strip()
    if not text:
        raise QueryLimitExceeded(
            "query is empty",
            hint="pass a non-empty read-only Cypher statement",
            details={"rule": "non-empty"},
        )
    if len(query) > max_length:
        raise QueryLimitExceeded(
            f"query length {len(query)} exceeds limit {max_length}",
            hint="shorten the query or narrow the pattern",
            details={"rule": "max-length", "length": len(query), "limit": max_length},
        )

    m = _WRITE.search(text)
    if m:
        raise QueryRejected(
            f"write keyword {m.group(1).upper()} is not allowed",
            hint="queries are read-only; use upsert_entities / upsert_relations to write",
            details={"rule": "no-write", "keyword": m.group(1).upper()},
        )

    if _FORBIDDEN_PREFIX.search(text):
        raise QueryRejected(
            "forbidden procedure namespace (apoc / dbms)",
            hint="APOC and dbms procedures are disabled entirely",
            details={"rule": "forbidden-namespace"},
        )

    for call in _CALL.findall(text):
        if call.lower() not in WHITELIST:
            raise QueryRejected(
                f"CALL {call} is not on the whitelist",
                hint="only built-in read-only introspection procedures are permitted",
                details={"rule": "call-whitelist", "procedure": call},
            )
