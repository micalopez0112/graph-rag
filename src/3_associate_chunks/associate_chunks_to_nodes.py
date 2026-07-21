"""
associate_chunks_to_nodes.py
-----------------------------
STEP 3 – Link PDF text chunks to graph nodes in Neptune.

WHY THIS IS THE CORE OF GraphRAG:
  Plain RAG retrieves relevant text chunks.
  GraphRAG ALSO retrieves the surrounding graph structure — neighbours,
  relationships, and properties of the nodes that a chunk references.
  This gives the LLM richer, more accurate context.

  Example:
    Chunk: "...fault code F-002 Low Oil Pressure on PP-ENG-001..."
    → Linked to Neptune node: node_engine
    → At query time: chatbot also fetches all edges of node_engine
      (controls governor, drives alternator, monitored by control panel)
    → LLM gets BOTH the text AND the graph context → better answer.

HOW WE ASSOCIATE CHUNKS TO NODES:
  Strategy 1 – EXACT MATCH: Look for RDS-PP codes in the chunk text (PP-ENG-001, etc.)
               and link to the node that has that pp_code property in Neptune.
               This is fast and reliable for structured documents.

  Strategy 2 – SEMANTIC MATCH: Embed the chunk, embed a description of each node,
               compute cosine similarity, and link if score > threshold.
               This catches implicit references.

  We use both strategies, storing the reason for each link.

HOW TO RUN:
  python src/3_associate_chunks/associate_chunks_to_nodes.py
"""

import os
import re
import json
import math
import psycopg2
import boto3
from pathlib import Path
from typing import List, Dict, Optional

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
AWS_REGION  = os.getenv("AWS_REGION", "us-east-1")
DB_HOST     = os.getenv("RDS_HOST",     "your-rds.xxxx.us-east-1.rds.amazonaws.com")
DB_PORT     = os.getenv("RDS_PORT",     "5432")
DB_NAME     = os.getenv("RDS_DB_NAME",  "graphrag")
DB_USER     = os.getenv("RDS_USER",     "graphrag_admin")
DB_PASSWORD = os.getenv("RDS_PASSWORD", "changeme")

SEMANTIC_THRESHOLD = float(os.getenv("SEMANTIC_THRESHOLD", "0.75"))  # cosine similarity cutoff

# The graph JSON — we read node metadata from here (not from Neptune) for simplicity.
GRAPH_JSON_PATH = Path(__file__).parent.parent.parent / "data" / "power_plant_graph.json"


# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------

def load_nodes() -> List[dict]:
    with open(GRAPH_JSON_PATH) as f:
        return json.load(f)["graph"]["nodes"]


def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )


def embed_text(text: str) -> List[float]:
    """Embed text using Amazon Bedrock Titan (same model used in chunk_and_embed.py)."""
    bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)
    body = json.dumps({"inputText": text})
    response = bedrock.invoke_model(
        modelId="amazon.titan-embed-text-v2:0", body=body,
        accept="application/json", contentType="application/json"
    )
    return json.loads(response["body"].read())["embedding"]


def cosine_similarity(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x ** 2 for x in a))
    mag_b = math.sqrt(sum(x ** 2 for x in b))
    if mag_a == 0 or mag_b == 0:
        return 0.0
    return dot / (mag_a * mag_b)


def upsert_link(cur, chunk_id: str, node_id: str, reason: str, score: Optional[float]):
    cur.execute("""
        INSERT INTO chunk_node_links (chunk_id, neptune_node_id, link_reason, relevance_score)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (chunk_id, neptune_node_id)
        DO UPDATE SET link_reason = EXCLUDED.link_reason,
                      relevance_score = EXCLUDED.relevance_score;
    """, (chunk_id, node_id, reason, score))


# ---------------------------------------------------------------------------
# STRATEGY 1 – EXACT PP CODE MATCHING
# ---------------------------------------------------------------------------

def build_code_to_node_map(nodes: List[dict]) -> Dict[str, str]:
    """Map 'PP-ENG-001' → 'node_engine', etc."""
    mapping = {}
    for node in nodes:
        code = node["properties"].get("pp_code")
        if code:
            mapping[code] = node["id"]
    return mapping


def associate_by_pp_codes(cur, nodes: List[dict]):
    """
    Find chunks that mention a PP code and link them to the matching node.
    e.g. chunk text contains 'PP-ENG-001'  → link to node_engine.
    """
    print("\n🔍 Strategy 1: Exact PP code matching...")
    code_map = build_code_to_node_map(nodes)
    # Build regex: PP-ENG-001 | PP-ALT-001 | ...
    pattern = re.compile("|".join(re.escape(c) for c in code_map.keys()))

    # Fetch all chunks
    cur.execute("SELECT chunk_id, text_content FROM document_chunks;")
    chunks = cur.fetchall()

    links_created = 0
    for chunk_id, text in chunks:
        found_codes = set(pattern.findall(text))
        for code in found_codes:
            node_id = code_map[code]
            upsert_link(cur, chunk_id, node_id, f"exact_pp_code:{code}", 1.0)
            links_created += 1
            print(f"  ✓ {chunk_id} ──[{code}]──▶ {node_id}")

    print(f"  Created {links_created} links via exact PP code match.")
    return links_created


# ---------------------------------------------------------------------------
# STRATEGY 2 – SEMANTIC SIMILARITY MATCHING
# ---------------------------------------------------------------------------

def associate_by_semantic_similarity(cur, nodes: List[dict]):
    """
    For each node, embed its description + name.
    For each chunk, compute cosine similarity with each node embedding.
    Link if similarity > SEMANTIC_THRESHOLD.

    This is heavier (many embedding API calls) so we run it only for chunks
    that weren't matched by strategy 1.
    """
    print(f"\n🧠 Strategy 2: Semantic similarity (threshold={SEMANTIC_THRESHOLD})...")

    # Find chunks not yet linked
    cur.execute("""
        SELECT dc.chunk_id, dc.text_content, ce.embedding
        FROM document_chunks dc
        JOIN chunk_embeddings ce ON dc.chunk_id = ce.chunk_id
        WHERE dc.chunk_id NOT IN (SELECT DISTINCT chunk_id FROM chunk_node_links);
    """)
    unlinked = cur.fetchall()

    if not unlinked:
        print("  All chunks already linked. Skipping semantic pass.")
        return 0

    print(f"  {len(unlinked)} unlinked chunks to process...")

    # Embed each node's description
    node_embeddings = []
    for node in nodes:
        desc = f"{node['properties']['name']}. {node['properties'].get('description', '')}"
        print(f"  Embedding node: {node['id']}")
        emb  = embed_text(desc)
        node_embeddings.append((node["id"], emb))

    links_created = 0
    for chunk_id, text, chunk_emb_str in unlinked:
        # Parse the embedding stored in pgvector (comes back as a string "[0.1,0.2,...]")
        chunk_emb = [float(x) for x in chunk_emb_str.strip("[]").split(",")]

        for node_id, node_emb in node_embeddings:
            score = cosine_similarity(chunk_emb, node_emb)
            if score >= SEMANTIC_THRESHOLD:
                upsert_link(cur, chunk_id, node_id, "semantic_similarity", round(score, 4))
                links_created += 1
                print(f"  ✓ {chunk_id} ──[{score:.3f}]──▶ {node_id}")

    print(f"  Created {links_created} links via semantic similarity.")
    return links_created


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------

def main():
    nodes = load_nodes()
    print(f"📂 Loaded {len(nodes)} graph nodes from JSON.")

    conn = get_db_connection()
    cur  = conn.cursor()

    total = 0
    total += associate_by_pp_codes(cur, nodes)
    conn.commit()

    total += associate_by_semantic_similarity(cur, nodes)
    conn.commit()

    # Summary
    cur.execute("SELECT COUNT(*) FROM chunk_node_links;")
    total_links = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT chunk_id) FROM chunk_node_links;")
    linked_chunks = cur.fetchone()[0]

    print(f"\n🎉 Association complete!")
    print(f"   Total links in DB   : {total_links}")
    print(f"   Chunks with ≥1 link : {linked_chunks}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
