"""Manage Neo4j driver connection (singleton)."""
import os

from neo4j import GraphDatabase

_driver = None


def get_neo4j_driver():
    """Return shared Neo4j driver initialized from environment variables."""
    global _driver
    if _driver is not None:
        if getattr(_driver, "_closed", False):
            _driver = None
        else:
            try:
                _driver.verify_connectivity()
                return _driver
            except Exception:
                _driver = None
    _driver = GraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"),
              os.getenv("NEO4J_PASSWORD", "vat-temporal-2026")),
    )
    return _driver


def close_driver():
    """Close the shared Neo4j driver instance."""
    global _driver
    if _driver is not None:
        _driver.close()
        _driver = None
