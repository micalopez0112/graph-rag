"""
graphrag_retriever.py
---------------------
STEP 4a – The GraphRAG retrieval engine.

This module is the BRAIN of the system. Given a user question, it:
  1. Embeds the question using the same model as the chunks.
  2. Queries pgvector for the top-K most semantically similar chunks (vector search).
  3. For each retrieved chunk, fetches the linked Neo4j graph nodes.
  4. For each graph node, queries Neo4j for its neighbours and relationships
     (graph traversal) — this is what makes it GraphRAG, not just RAG.
  5. Assembles all the context (text + graph) into a structured prompt.
  6. Sends it to the LLM (Claude via Bedrock, or GPT-4 via OpenAI).
  7. Returns the answer.
"""

import os
import json
import boto3
import psycopg2
from typing import List, Tuple, Optional
from neo4j import GraphDatabase

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
AWS_REGION     = os.getenv("AWS_REGION",      "us-east-1")
NEO4J_URI      = os.getenv("NEO4J_URI",       "bolt://localhost:7687")
NEO4J_USER     = os.getenv("NEO4J_USER",      "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD",  "graphrag123")

DB_HOST        = os.getenv("RDS_HOST",        "your-rds.xxxx.us-east-1.rds.amazonaws.com")
DB_PORT        = os.getenv("RDS_PORT",        "5432")
DB_NAME        = os.getenv("RDS_DB_NAME",     "graphrag")
DB_USER        = os.getenv("RDS_USER",        "graphrag_admin")
DB_PASSWORD    = os.getenv("RDS_PASSWORD",    "changeme")

TOP_K_CHUNKS   = int(os.getenv("TOP_K_CHUNKS",  "5"))
LLM_PROVIDER   = os.getenv("LLM_PROVIDER",    "bedrock")   # "bedrock" or "openai"
EMBEDDING_DIM  = int(os.getenv("EMBEDDING_DIM", "1536"))


# ---------------------------------------------------------------------------
# CONNECTIONS
# ---------------------------------------------------------------------------

class GraphRAGRetriever:
    def __init__(self):
        print("🔌 Initialising GraphRAG Retriever...")
        self._db_conn      = None
        self._neo4j_driver = None
        self._bedrock      = boto3.client("bedrock-runtime", region_name=AWS_REGION)
        print("✅ Retriever ready.")

    # ── PostgreSQL (RDS) ──────────────────────────────────────────────────────
    @property
    def db(self):
        if self._db_conn is None or self._db_conn.closed:
            self._db_conn = psycopg2.connect(
                host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
                user=DB_USER, password=DB_PASSWORD
            )
        return self._db_conn

    # ── Neo4j ─────────────────────────────────────────────────────────────────
    @property
    def neo4j(self):
        if self._neo4j_driver is None:
            self._neo4j_driver = GraphDatabase.driver(
                NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD)
            )
            self._neo4j_driver.verify_connectivity()
        return self._neo4j_driver

    def close(self):
        if self._db_conn:
            self._db_conn.close()
        if self._neo4j_driver:
            self._neo4j_driver.close()

    # ── Embedding ────────────────────────────────────────────────────────────
    def embed_query(self, text: str) -> List[float]:
        """Embed the user query using Amazon Bedrock Titan."""
        body = json.dumps({"inputText": text})
        response = self._bedrock.invoke_model(
            modelId="amazon.titan-embed-text-v2:0", body=body,
            accept="application/json", contentType="application/json"
        )
        return json.loads(response["body"].read())["embedding"]

    # ── Step 1: Vector Search ─────────────────────────────────────────────────
    def vector_search(self, query_embedding: List[float], top_k: int = TOP_K_CHUNKS) -> List[dict]:
        """
        Find the most semantically similar chunks to the query using pgvector.
        Uses cosine distance (<=> operator in pgvector).
        """
        vec_str = "[" + ",".join(str(x) for x in query_embedding) + "]"
        cur = self.db.cursor()
        cur.execute("""
            SELECT
                dc.chunk_id,
                dc.text_content,
                dc.page_number,
                dc.source_file,
                1 - (ce.embedding <=> %s::vector) AS cosine_similarity
            FROM chunk_embeddings ce
            JOIN document_chunks dc ON ce.chunk_id = dc.chunk_id
            ORDER BY ce.embedding <=> %s::vector
            LIMIT %s;
        """, (vec_str, vec_str, top_k))
        rows = cur.fetchall()
        cur.close()
        return [
            {"chunk_id": r[0], "text": r[1], "page": r[2], "source": r[3], "score": r[4]}
            for r in rows
        ]

    # ── Step 2: Graph Context Retrieval ───────────────────────────────────────
    def get_linked_nodes(self, chunk_ids: List[str]) -> List[str]:
        """Get Neo4j node IDs linked to these chunks."""
        if not chunk_ids:
            return []
        cur = self.db.cursor()
        cur.execute("""
            SELECT DISTINCT neptune_node_id
            FROM chunk_node_links
            WHERE chunk_id = ANY(%s)
            ORDER BY relevance_score DESC NULLS LAST;
        """, (chunk_ids,))
        rows = cur.fetchall()
        cur.close()
        return [r[0] for r in rows]

    def get_node_graph_context(self, node_id: str) -> dict:
        """
        Query Neo4j for a node and all its direct relationships using Cypher.
        This is the graph traversal step that makes GraphRAG richer than plain RAG.
        """
        cypher = """
            MATCH (n {id: $node_id})
            OPTIONAL MATCH (n)-[r_out]->(target)
            OPTIONAL MATCH (source)-[r_in]->(n)
            RETURN
                n,
                labels(n)[0] AS node_label,
                collect(DISTINCT {
                    relationship: type(r_out),
                    target_id:    target.id,
                    target_name:  target.name,
                    target_label: labels(target)[0]
                }) AS outgoing,
                collect(DISTINCT {
                    relationship: type(r_in),
                    source_id:    source.id,
                    source_label: labels(source)[0]
                }) AS incoming
        """
        try:
            with self.neo4j.session() as session:
                record = session.run(cypher, node_id=node_id).single()
                if record is None:
                    return {"node_id": node_id, "error": "node not found in Neo4j"}
                return {
                    "node_id":        node_id,
                    "node_label":     record["node_label"],
                    "properties":     dict(record["n"]),
                    "outgoing_edges": [e for e in record["outgoing"] if e.get("target_id")],
                    "incoming_edges": [e for e in record["incoming"] if e.get("source_id")],
                }
        except Exception as e:
            return {"node_id": node_id, "error": str(e)}

    # ── Step 3: Assemble Context ───────────────────────────────────────────────
    def retrieve(self, question: str) -> Tuple[str, dict]:
        """
        Full GraphRAG retrieval pipeline.
        Returns: (context_string_for_llm, debug_info_dict)
        """
        print(f"\n🔎 Retrieving context for: '{question}'")

        # 1. Embed question
        q_emb = self.embed_query(question)

        # 2. Vector search → relevant text chunks
        chunks = self.vector_search(q_emb)
        print(f"  📄 Retrieved {len(chunks)} text chunks.")

        # 3. Find graph nodes linked to these chunks
        chunk_ids = [c["chunk_id"] for c in chunks]
        node_ids  = self.get_linked_nodes(chunk_ids)
        print(f"  🔗 Found {len(node_ids)} linked graph nodes: {node_ids}")

        # 4. Fetch graph context for each node
        graph_contexts = []
        for nid in node_ids:
            ctx = self.get_node_graph_context(nid)
            graph_contexts.append(ctx)

        # 5. Build the context string
        context_parts = []
        context_parts.append("=== RELEVANT DOCUMENT EXCERPTS ===")
        for i, c in enumerate(chunks, 1):
            context_parts.append(f"\n[Chunk {i} | Page {c['page']} | Score {c['score']:.3f}]")
            context_parts.append(c["text"])

        context_parts.append("\n\n=== RELATED POWER PLANT COMPONENTS (from Knowledge Graph) ===")
        for gc in graph_contexts:
            if "error" in gc:
                continue
            props = gc["properties"]
            context_parts.append(f"\n[Component: {props.get('name', gc['node_id'])} | Type: {gc['node_label']} | Code: {props.get('pp_code', 'N/A')}]")
            context_parts.append(f"  Description: {props.get('description', 'N/A')}")
            if gc["outgoing_edges"]:
                rels = ", ".join(f"{e['relationship']} → {e['target_name']}" for e in gc["outgoing_edges"])
                context_parts.append(f"  Connects to: {rels}")
            if gc["incoming_edges"]:
                rels = ", ".join(f"{e['source_label']} → {e['relationship']}" for e in gc["incoming_edges"])
                context_parts.append(f"  Connected from: {rels}")

        context_string = "\n".join(context_parts)

        debug = {
            "chunks_retrieved": len(chunks),
            "nodes_retrieved":  len(graph_contexts),
            "node_ids":         node_ids,
            "top_chunk_score":  chunks[0]["score"] if chunks else None,
        }

        return context_string, debug


# ---------------------------------------------------------------------------
# LLM CALL
# ---------------------------------------------------------------------------

def call_llm_bedrock(question: str, context: str) -> str:
    """
    Call Amazon Nova Lite via Amazon Bedrock Converse API.
    Nova Lite is AWS's own fast, cost-effective LLM — fully ACTIVE, no Anthropic
    approval needed. Uses the Converse API which is the recommended modern interface.
    Your IAM role needs: AmazonBedrockFullAccess
    """
    bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

    system_prompt = (
        "You are a helpful engineering assistant specialised in power plant operations. "
        "You have access to both structured knowledge (from a graph of plant components and their relationships) "
        "and relevant document excerpts. Use BOTH sources of information to give accurate, concise answers. "
        "Always mention the RDS-PP code of components when relevant. "
        "If the context does not contain the answer, say so — do not make up information."
    )

    user_message = (
        f"Based on the following context from our power plant knowledge base, "
        f"please answer this question:\n\nQUESTION: {question}\n\nCONTEXT:\n{context}\n\n"
        f"Please provide a clear, technical answer."
    )

    response = bedrock.converse(
        modelId="amazon.nova-lite-v1:0",
        system=[{"text": system_prompt}],
        messages=[{"role": "user", "content": [{"text": user_message}]}],
        inferenceConfig={"maxTokens": 1024, "temperature": 0.1},
    )
    return response["output"]["message"]["content"][0]["text"]


def call_llm_openai(question: str, context: str) -> str:
    """Call GPT-4 via OpenAI API (alternative to Bedrock)."""
    import openai
    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[
            {"role": "system", "content": "You are a helpful engineering assistant specialised in power plant operations. Use the provided context to answer questions accurately."},
            {"role": "user", "content": f"Context:\n{context}\n\nQuestion: {question}"}
        ],
        max_tokens=1024,
    )
    return response.choices[0].message.content
