"""
upload_to_neptune.py
--------------------
STEP 1 – Upload the power plant graph to AWS Neptune using Gremlin.

WHY NEPTUNE?
  AWS Neptune is a fully managed graph database. It speaks Gremlin (Apache TinkerPop),
  which is a query language for graph traversal. We translate our JSON graph into
  Gremlin "addV" (add vertex) and "addE" (add edge) commands.

WHY NOT NEPTUNE ANALYTICS?
  Neptune Analytics is optimised for bulk analytics and uses a different API.
  Neptune (the original service) gives you full control over every vertex and edge
  property, supports Gremlin queries, and lets you query the graph in real-time
  from application code — which is what we need for GraphRAG.

PREREQUISITES (AWS side – see README for how to set these up):
  1. A Neptune cluster is running in a private VPC.
  2. You are running this script from an EC2 instance or Cloud9 IDE that is IN
     the same VPC, OR you have set up an SSH tunnel to the Neptune endpoint.
     Neptune does NOT expose a public endpoint by default (security best practice).
  3. The IAM role of the EC2/Cloud9 has "NeptuneFullAccess" or the equivalent policy.

HOW TO RUN:
  python src/1_upload_graph/upload_to_neptune.py
"""

import json
import os
import sys
import time
from pathlib import Path

from gremlin_python.driver import client, serializer
from gremlin_python.driver.driver_remote_connection import DriverRemoteConnection
from gremlin_python.process.anonymous_traversal import traversal
from gremlin_python.process.graph_traversal import __
from gremlin_python.process.traversal import T, Cardinality

# ---------------------------------------------------------------------------
# CONFIGURATION  – edit these or set as environment variables
# ---------------------------------------------------------------------------
NEPTUNE_ENDPOINT = os.getenv("NEPTUNE_ENDPOINT", "wss://your-neptune-cluster.cluster-xxxx.us-east-1.neptune.amazonaws.com:8182/gremlin")
# ^ Format: wss://<cluster-endpoint>:8182/gremlin
# You find this in the AWS Console → Neptune → Clusters → your cluster → "Writer endpoint"

GRAPH_JSON_PATH = Path(__file__).parent.parent.parent / "data" / "power_plant_graph.json"


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def load_graph(path: Path) -> dict:
    """Load our hand-crafted JSON graph."""
    with open(path) as f:
        return json.load(f)["graph"]


def connect_to_neptune(endpoint: str):
    """
    Open a Gremlin connection to Neptune.
    Neptune requires WSS (WebSocket Secure) connections.
    """
    print(f"🔌 Connecting to Neptune at: {endpoint}")
    connection = DriverRemoteConnection(
        endpoint,
        "g",
        message_serializer=serializer.GraphSONSerializersV2d0(),
    )
    g = traversal().withRemote(connection)
    print("✅ Connected to Neptune.")
    return g, connection


def clear_existing_graph(g):
    """
    Drop all existing vertices (and their edges) so we start fresh.
    WARNING: This deletes everything in the graph.
    """
    print("🗑️  Clearing existing graph data...")
    g.V().drop().iterate()
    print("✅ Graph cleared.")


def upload_nodes(g, nodes: list):
    """
    Translate each JSON node into a Gremlin addV() command.

    Gremlin vertex creation:
      g.addV('<label>').property(id, '<id>').property('<key>', '<value>') ...

    We store every property from the JSON directly as a vertex property.
    Complex values (lists) are stored as JSON strings so Neptune can hold them.
    """
    print(f"\n📤 Uploading {len(nodes)} nodes...")
    for node in nodes:
        node_id = node["id"]
        label   = node["label"]
        props   = node["properties"]

        print(f"  ➕ Adding vertex: [{label}] id={node_id} name={props.get('name','')}")

        traversal_step = g.addV(label).property(T.id, node_id)

        for key, value in props.items():
            # Neptune doesn't support list properties directly in Gremlin single-cardinality;
            # we serialise lists/dicts as JSON strings.
            if isinstance(value, (list, dict)):
                value = json.dumps(value)
            traversal_step = traversal_step.property(Cardinality.single, key, str(value))

        traversal_step.next()   # actually execute
        time.sleep(0.05)        # small pause to avoid overwhelming Neptune

    print(f"✅ {len(nodes)} nodes uploaded.")


def upload_edges(g, edges: list):
    """
    Translate each JSON edge into a Gremlin addE() command.

    Gremlin edge creation:
      g.V('<from_id>').addE('<label>').to(__.V('<to_id>')).property('<key>','<value>')
    """
    print(f"\n📤 Uploading {len(edges)} edges...")
    for edge in edges:
        edge_id   = edge["id"]
        from_id   = edge["from"]
        to_id     = edge["to"]
        label     = edge["label"]
        props     = edge.get("properties", {})

        print(f"  ➕ Adding edge: [{label}] {from_id} ──▶ {to_id}")

        traversal_step = (
            g.V(from_id)
             .addE(label)
             .to(__.V(to_id))
             .property(T.id, edge_id)
        )

        for key, value in props.items():
            if isinstance(value, (list, dict)):
                value = json.dumps(value)
            traversal_step = traversal_step.property(key, str(value))

        traversal_step.next()
        time.sleep(0.05)

    print(f"✅ {len(edges)} edges uploaded.")


def verify_upload(g):
    """Run a quick verification query to confirm the data is in Neptune."""
    print("\n🔍 Verifying upload...")
    vertex_count = g.V().count().next()
    edge_count   = g.E().count().next()
    print(f"  Graph contains: {vertex_count} vertices, {edge_count} edges")

    print("\n  Sample vertices:")
    sample = g.V().limit(3).valueMap(True).toList()
    for v in sample:
        print(f"    id={v.get(T.id)}, label={v.get(T.label)}, name={v.get('name', ['?'])[0]}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    if "your-neptune-cluster" in NEPTUNE_ENDPOINT:
        print("❌ ERROR: Please set the NEPTUNE_ENDPOINT environment variable.")
        print("   Example:")
        print("   export NEPTUNE_ENDPOINT='wss://mycluster.cluster-abc.us-east-1.neptune.amazonaws.com:8182/gremlin'")
        sys.exit(1)

    graph = load_graph(GRAPH_JSON_PATH)
    print(f"📂 Loaded graph: '{graph['name']}' with {len(graph['nodes'])} nodes and {len(graph['edges'])} edges.")

    g, connection = connect_to_neptune(NEPTUNE_ENDPOINT)

    try:
        clear_existing_graph(g)
        upload_nodes(g, graph["nodes"])
        upload_edges(g, graph["edges"])
        verify_upload(g)
        print("\n🎉 Graph successfully uploaded to AWS Neptune!")
    finally:
        connection.close()
        print("🔌 Connection closed.")


if __name__ == "__main__":
    main()
