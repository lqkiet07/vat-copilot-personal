import pytest
from src.graph.connection import close_driver


@pytest.fixture(scope="session", autouse=True)
def cleanup_neo4j_driver():
    yield
    close_driver()
