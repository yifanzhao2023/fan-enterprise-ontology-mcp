"""One Neo4j per test module, from testcontainers.
Enterprise is required for RBAC (CREATE ROLE / GRANT). Neo4j licenses Enterprise
free of charge for development, testing and non-production use.
"""
import pytest
from testcontainers.community.neo4j import Neo4jContainer
from feomcp.config import Settings
from feomcp.neo4j_store import Neo4jStore

@pytest.fixture(scope="module")
def store():
    container = Neo4jContainer(
        "neo4j:5.26-enterprise", username="neo4j", password="test-password-123"
    ).with_env("NEO4J_ACCEPT_LICENSE_AGREEMENT", "yes")
    with container:
        container.start()
        uri = container.get_connection_url()
        settings = Settings.from_env({
            "FEOMCP_NEO4J_URI": uri,
            "FEOMCP_NEO4J_USER": "neo4j",
            "FEOMCP_NEO4J_PASSWORD": "test-password-123",
            "FEOMCP_MAX_BATCH": "100",
        })
        s = Neo4jStore(settings)
        yield s
        s.close()
