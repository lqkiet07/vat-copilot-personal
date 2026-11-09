import os
import socket
from urllib.parse import urlsplit

import pytest
from src.graph.connection import close_driver


def configure_test_neo4j():
    uri = os.getenv("NEO4J_TEST_URI", "bolt://127.0.0.1:17687")
    parsed = urlsplit(uri)
    if parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise pytest.UsageError("Neo4j tests require a localhost test container")
    if parsed.scheme != "bolt" or parsed.port != 17687:
        raise pytest.UsageError("Neo4j tests require bolt port 17687, never application port 7687")
    close_driver()
    os.environ["NEO4J_URI"] = uri
    os.environ["NEO4J_USER"] = os.getenv("NEO4J_TEST_USER", "neo4j")
    os.environ["NEO4J_PASSWORD"] = os.getenv("NEO4J_TEST_PASSWORD", "")
    return parsed.hostname, parsed.port


def pytest_configure(config):
    configure_test_neo4j()


def _neo4j_up(host: str = "127.0.0.1", port: int = 17687, timeout: float = 0.5) -> bool:
    with socket.socket() as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def pytest_collection_modifyitems(config, items):
    host, port = configure_test_neo4j()
    if os.getenv("NEO4J_TEST_PASSWORD") and _neo4j_up(host, port):
        return
    skip = pytest.mark.skip(reason="Isolated Neo4j test container or NEO4J_TEST_PASSWORD unavailable")
    for item in items:
        if "requires_neo4j" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def cleanup_neo4j_driver():
    yield
    close_driver()
