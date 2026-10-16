import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class Settings:
    store: str
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    neo4j_write_user: str
    neo4j_write_password: str
    ontology_dir: Path
    query_timeout_ms: int
    max_rows: int
    max_query_length: int
    max_batch: int
    redact_fields: tuple[str, ...]
    allow_shared_account: bool

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        e = os.environ if env is None else env

        def s(key: str, default: str) -> str:
            return e.get(key, default)

        read_user = s("BEOMCP_NEO4J_USER", "neo4j")
        read_pw = s("BEOMCP_NEO4J_PASSWORD", "")
        settings = cls(
            store=s("BEOMCP_STORE", "neo4j"),
            neo4j_uri=s("BEOMCP_NEO4J_URI", "bolt://localhost:7687"),
            neo4j_user=read_user,
            neo4j_password=read_pw,
            neo4j_write_user=s("BEOMCP_NEO4J_WRITE_USER", read_user),
            neo4j_write_password=s("BEOMCP_NEO4J_WRITE_PASSWORD", read_pw),
            ontology_dir=Path(s("BEOMCP_ONTOLOGY_DIR", "ontology")),
            query_timeout_ms=int(s("BEOMCP_QUERY_TIMEOUT_MS", "10000")),
            max_rows=int(s("BEOMCP_MAX_ROWS", "50")),
            max_query_length=int(s("BEOMCP_MAX_QUERY_LENGTH", "8192")),
            max_batch=int(s("BEOMCP_MAX_BATCH", "500")),
            redact_fields=tuple(
                f.strip() for f in s("BEOMCP_REDACT_FIELDS", "").split(",") if f.strip()
            ),
            allow_shared_account=s("BEOMCP_ALLOW_SHARED_ACCOUNT", "").lower()
            in ("1", "true", "yes"),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        """Fail at startup, not at first query. A wrong URI or a zero row cap is a
        configuration mistake and must not become a runtime mystery."""
        if not self.neo4j_uri.startswith(("bolt://", "neo4j://", "bolt+s://", "neo4j+s://")):
            raise ValueError(
                f"BEOMCP_NEO4J_URI must be a bolt:// or neo4j:// URI, got {self.neo4j_uri!r}"
            )
        for name in ("query_timeout_ms", "max_rows", "max_query_length", "max_batch"):
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0, got {getattr(self, name)}")
