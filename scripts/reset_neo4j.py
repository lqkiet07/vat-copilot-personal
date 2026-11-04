"""Script to completely wipe nodes and relationships from Neo4j."""
import argparse
import sys
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.graph.connection import get_neo4j_driver


def reset_neo4j(confirm: bool = False) -> int:
    """Delete all nodes and relationships in Neo4j."""
    if not confirm:
        raise ValueError("Must provide confirm=True or --yes to reset Neo4j")

    driver = get_neo4j_driver()
    with driver.session() as session:
        result = session.run("MATCH (n) DETACH DELETE n")
        summary = result.consume()
        deleted = summary.counters.nodes_deleted
        print(f"Reset Neo4j: deleted {deleted} nodes and associated relationships.")
        return deleted


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wipe all Neo4j nodes and relationships.")
    parser.add_argument("--yes", action="store_true", help="Confirm database reset")
    args = parser.parse_args()

    if not args.yes:
        print("Error: must pass --yes to confirm resetting Neo4j.")
        sys.exit(1)

    reset_neo4j(confirm=True)
