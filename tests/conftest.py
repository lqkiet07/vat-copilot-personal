import socket
import pytest
from src.graph.connection import close_driver


def _neo4j_up(host: str = "127.0.0.1", port: int = 7687, timeout: float = 0.5) -> bool:
    with socket.socket() as s:
        s.settimeout(timeout)
        return s.connect_ex((host, port)) == 0


def pytest_collection_modifyitems(config, items):
    if _neo4j_up():
        return
    skip = pytest.mark.skip(reason="Neo4j not running on bolt://localhost:7687")
    for item in items:
        if "requires_neo4j" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def cleanup_neo4j_driver():
    yield
    close_driver()
