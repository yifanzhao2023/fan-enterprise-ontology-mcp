"""Static Cypher safety checks.

Called only from QueryEngine.submit — one choke point for every caller, so no
code path can forget it. Named queries and raw Cypher go through the same guard;
templates get no back door.

The checks run on a normalised view of the query, not the raw text: comments and
string literals are removed so `'delete me'` is not read as a DELETE, backticked
identifiers are blanked so a property called `set` is not read as a SET, and the
call view unquotes backticks so ``CALL `apoc.x` `` is seen rather than skipped.
"""

import re

from .errors import QueryLimitExceeded, QueryRejected

_WRITE = re.compile(
    r"\b(CREATE|MERGE|DELETE|DETACH|SET|REMOVE|DROP|LOAD\s+CSV)\b",
    re.IGNORECASE,
)
_FORBIDDEN_PREFIX = re.compile(r"\b(apoc|dbms)\b", re.IGNORECASE)
_CALL_TARGET = re.compile(r"(?<!\.)\bCALL\s+([A-Za-z0-9_.]+)", re.IGNORECASE)
_CALL_TOKEN = re.compile(r"(?<!\.)\bCALL\b", re.IGNORECASE)

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_LINE_COMMENT = re.compile(r"//[^\n]*")
_STRING = re.compile(r"'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"")
_BACKTICK_IDENT = re.compile(r"`[^`]*`")

# default deny — only these read-only introspection procedures are permitted
WHITELIST = {
    "db.labels",
    "db.propertyKeys",
    "db.relationshipTypes",
    "db.schema.relTypeNames",
    "db.schema.nodeTypeNames",
}


def _without_comments_and_strings(query: str) -> str:
    text = _BLOCK_COMMENT.sub(" ", query)
    text = _LINE_COMMENT.sub(" ", text)
    return _STRING.sub(" '' ", text)


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

    bare = _without_comments_and_strings(text)
    # backticked names are identifiers, never keywords
    keyword_view = _BACKTICK_IDENT.sub(" _ident_ ", bare)

    m = _WRITE.search(keyword_view)
    if m:
        raise QueryRejected(
            f"write keyword {m.group(1).upper()} is not allowed",
            hint="queries are read-only; use upsert_entities / upsert_relations to write",
            details={"rule": "no-write", "keyword": m.group(1).upper()},
        )

    if _FORBIDDEN_PREFIX.search(keyword_view):
        raise QueryRejected(
            "forbidden procedure namespace (apoc / dbms)",
            hint="APOC and dbms procedures are disabled entirely",
            details={"rule": "forbidden-namespace"},
        )

    # unquoted view so ``CALL `x.y` `` is matched rather than skipped
    call_view = _BACKTICK_IDENT.sub(lambda m: " " + m.group(0)[1:-1] + " ", bare)
    tokens = _CALL_TOKEN.findall(call_view)
    targets = _CALL_TARGET.findall(call_view)
    if len(targets) != len(tokens):
        raise QueryRejected(
            "unrecognised CALL form",
            hint="only built-in read-only introspection procedures are permitted",
            details={"rule": "call-whitelist", "procedure": "<unparsed>"},
        )
    for call in targets:
        if call.lower() not in WHITELIST:
            raise QueryRejected(
                f"CALL {call} is not on the whitelist",
                hint="only built-in read-only introspection procedures are permitted",
                details={"rule": "call-whitelist", "procedure": call},
            )
