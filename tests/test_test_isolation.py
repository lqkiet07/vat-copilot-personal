import pytest

from tests import conftest


def configure_test_neo4j():
    return conftest.configure_test_neo4j()


def test_test_database_rejects_application_port(monkeypatch):
    monkeypatch.setenv("NEO4J_TEST_URI", "bolt://localhost:7687")
    with pytest.raises(pytest.UsageError, match="17687"):
        configure_test_neo4j()


def test_test_database_rejects_remote_host(monkeypatch):
    monkeypatch.setenv("NEO4J_TEST_URI", "bolt://production.example:17687")
    with pytest.raises(pytest.UsageError, match="localhost"):
        configure_test_neo4j()


def test_test_database_overrides_application_credentials(monkeypatch):
    import os

    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_PASSWORD", "application-password")
    monkeypatch.setenv("NEO4J_TEST_URI", "bolt://127.0.0.1:17687")
    monkeypatch.setenv("NEO4J_TEST_PASSWORD", "test-password")
    monkeypatch.setenv("NEO4J_TEST_USER", "neo4j")
    configure_test_neo4j()
    assert os.environ["NEO4J_URI"] == "bolt://127.0.0.1:17687"
    assert os.environ["NEO4J_PASSWORD"] == "test-password"
