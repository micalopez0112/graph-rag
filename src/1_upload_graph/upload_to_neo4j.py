"""
upload_to_neo4j.py
------------------
STEP 1 – Upload the power plant graph to Neo4j using Cypher.

WHY NEO4J?
  Neo4j is the world's most popular graph database. It uses Cypher as its
  query language — a very readable, SQL-like language designed for graphs.
  We run it locally in Docker (free) and can later migrate to AWS Neptune
  (which also supports openCypher) when ready for production.

WHAT THIS SCRIPT DOES:
  1. Reads power_plant_graph.json
  2. For each node  → runs: MERGE (n:Label {id: ...}) SET n += {properties}
  3. For each edge  → runs: MATCH (a), (b) MERGE (a)-[:LABEL {id: ...}]->(b)
  4. Verifies the upload

PREREQUISITES:
  Neo4j must be running. Start it with:
    docker run \
      --name neo4j-graphrag \
      -p 7474:7474 -p 7687:7687 \
      -e NEO4J_AUTH=neo4j/graphrag123 \
      neo4j:5

  Then visit http://localhost:7474 to see the browser UI.

HOW TO RUN:
  python src/1_upload_graph/upload_to_neo4j.py
"""

import json
import os
import sys
from pathlib import Path
from neo4j import GraphDatabase

# ---------------------------------------------------------------------------
# CONFIGURATION – set via environment variables or edit defaults below
# ---------------------------------------------------------------------------
NEO4J_URI      = os.getenv("NEO4J_URI",      "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",     "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "graphrag123")

GRAPH_JSON_PATH = Path(__file__).parent.parent.parent / "data" / "power_plant_graph.json"


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def load_graph(path: Path) -> dict:
    with open(path) as f:
        return json.load(f)["graph"]


def clear_graph(session):
    """Delete all nodes and relationships. Safe for dev — starts fresh each run."""
    print("🗑️  Clearing existing graph data...")
    session.run("MATCH (n) DETACH DELETE n")
    print("✅ Graph cleared.")


def upload_nodes(session, nodes: list):
    """
    Create each node in Neo4j using MERGE (creates if not exists, updates if it does).
    We store every property from the JSON directly on the node.
    Lists are converted to JSON strings because Neo4j only stores scalar/array primitives.

    Cypher:  MERGE (n:Label {id: $id})  SET n += $props
    """
    print(f"\n📤 Uploading {len(nodes)} nodes...")
    for node in nodes:
        node_id = node["id"]
        label   = node["label"]
        props   = {k: (json.dumps(v) if isinstance(v, (list, dict)) else v)
                   for k, v in node["properties"].items()}
        props["id"] = node_id  # store id as a property too for easy lookup

        session.run(
            f"MERGE (n:{label} {{id: $id}}) SET n += $props",
            id=node_id, props=props
        )
        print(f"  ➕ [{label}] {node_id} — {props.get('name', '')}")

    print(f"✅ {len(nodes)} nodes uploaded.")


def upload_edges(session, edges: list):
    """
    Create each relationship using MERGE.
    We first MATCH the two endpoint nodes by id, then MERGE the relationship.

    Cypher:
      MATCH (a {id: $from_id}), (b {id: $to_id})
      MERGE (a)-[r:LABEL {id: $edge_id}]->(b)
      SET r += $props
    """
    print(f"\n📤 Uploading {len(edges)} edges...")
    for edge in edges:
        edge_id = edge["id"]
        from_id = edge["from"]
        to_id   = edge["to"]
        label   = edge["label"]   # e.g. MECHANICALLY_COUPLED
        props   = {k: (json.dumps(v) if isinstance(v, (list, dict)) else v)
                   for k, v in edge.get("properties", {}).items()}
        props["id"] = edge_id

        result = session.run(
            f"""
            MATCH (a {{id: $from_id}}), (b {{id: $to_id}})
            MERGE (a)-[r:{label} {{id: $edge_id}}]->(b)
            SET r += $props
            RETURN r
            """,
            from_id=from_id, to_id=to_id, edge_id=edge_id, props=props
        )
        if result.peek() is None:
            print(f"  ⚠️  Could not create edge {edge_id}: nodes not found ({from_id} → {to_id})")
        else:
            print(f"  ➕ [{label}] {from_id} ──▶ {to_id}")

    print(f"✅ {len(edges)} edges uploaded.")


def verify_upload(session):
    node_count = session.run("MATCH (n) RETURN count(n) AS c").single()["c"]
    edge_count = session.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
    print(f"\n🔍 Verification: {node_count} nodes, {edge_count} relationships in Neo4j.")

    print("\n  Sample nodes:")
    rows = session.run("MATCH (n) RETURN n.id AS id, labels(n)[0] AS label, n.name AS name LIMIT 3")
    for row in rows:
        print(f"    id={row['id']}, label={row['label']}, name={row['name']}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    graph = load_graph(GRAPH_JSON_PATH)
    print(f"📂 Loaded graph: '{graph['name']}' — {len(graph['nodes'])} nodes, {len(graph['edges'])} edges.")

    print(f"\n🔌 Connecting to Neo4j at {NEO4J_URI}...")
    driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

    try:
        driver.verify_connectivity()
        print("✅ Connected to Neo4j.")
    except Exception as e:
        print(f"❌ Could not connect to Neo4j: {e}")
        print("   Is Docker running? Try:  docker start neo4j-graphrag")
        sys.exit(1)

    with driver.session() as session:
        clear_graph(session)
        upload_nodes(session, graph["nodes"])
        upload_edges(session, graph["edges"])
        verify_upload(session)

    driver.close()
    print("\n🎉 Graph successfully uploaded to Neo4j!")
    print("   Open the browser UI at: http://localhost:7474")
    print("   Login: neo4j / graphrag123")
    print("   Try:   MATCH (n)-[r]->(m) RETURN n,r,m")


if __name__ == "__main__":
    main()
