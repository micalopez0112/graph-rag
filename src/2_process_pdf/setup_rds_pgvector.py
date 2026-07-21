"""
setup_rds_pgvector.py
---------------------
STEP 2a – Set up the AWS RDS PostgreSQL database with the pgvector extension.

WHY RDS POSTGRESQL + PGVECTOR?
  - RDS is AWS's fully managed relational database — no server management needed.
  - pgvector is a PostgreSQL extension that adds vector similarity search.
  - Storing vectors in the same DB as metadata keeps things simple and queryable
    with plain SQL (no extra infrastructure like OpenSearch or Pinecone needed).
  - pgvector supports cosine similarity search, which is what RAG systems use
    to find "semantically similar" text chunks.

WHAT THIS SCRIPT DOES:
  1. Connects to the RDS PostgreSQL instance you created on AWS.
  2. Enables the pgvector extension.
  3. Creates the tables needed for the GraphRAG system:
     - `document_chunks`  : stores raw text chunks from the PDF
     - `chunk_embeddings` : stores the vector embeddings of each chunk
     - `chunk_node_links` : links chunks to Neptune graph node IDs

HOW TO RUN:
  python src/2_process_pdf/setup_rds_pgvector.py
"""

import os
import psycopg2
from psycopg2.extensions import ISOLATION_LEVEL_AUTOCOMMIT

# ---------------------------------------------------------------------------
# CONFIGURATION – set these as environment variables (see .env.example)
# ---------------------------------------------------------------------------
DB_HOST     = os.getenv("RDS_HOST",     "your-rds-instance.xxxx.us-east-1.rds.amazonaws.com")
DB_PORT     = os.getenv("RDS_PORT",     "5432")
DB_NAME     = os.getenv("RDS_DB_NAME",  "graphrag")
DB_USER     = os.getenv("RDS_USER",     "graphrag_admin")
DB_PASSWORD = os.getenv("RDS_PASSWORD", "changeme")

# The embedding dimension from your embedding model.
# text-embedding-ada-002 (OpenAI) = 1536 dimensions
# amazon.titan-embed-text-v1 (Bedrock) = 1536 dimensions
# all-MiniLM-L6-v2 (local/HuggingFace) = 384 dimensions
EMBEDDING_DIM = int(os.getenv("EMBEDDING_DIM", "1536"))


def get_connection():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD,
        connect_timeout=10
    )


def setup_database():
    print(f"🔌 Connecting to RDS at {DB_HOST}:{DB_PORT}/{DB_NAME}...")
    conn = get_connection()
    conn.set_isolation_level(ISOLATION_LEVEL_AUTOCOMMIT)
    cur = conn.cursor()
    print("✅ Connected.")

    # ── 1. Enable pgvector ──────────────────────────────────────────────────
    print("\n📦 Enabling pgvector extension...")
    cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    print("✅ pgvector enabled.")

    # ── 2. Table: document_chunks ───────────────────────────────────────────
    # Stores the raw text of each chunk from the PDF, plus metadata.
    print("\n📋 Creating table: document_chunks ...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id              SERIAL PRIMARY KEY,
            chunk_id        TEXT UNIQUE NOT NULL,   -- e.g. "chunk_0042"
            source_file     TEXT NOT NULL,           -- PDF filename
            page_number     INT,                     -- PDF page the chunk came from
            chunk_index     INT,                     -- chunk sequence number within page
            text_content    TEXT NOT NULL,           -- the actual text
            char_count      INT,                     -- character length
            created_at      TIMESTAMPTZ DEFAULT NOW()
        );
    """)
    print("✅ document_chunks ready.")

    # ── 3. Table: chunk_embeddings ──────────────────────────────────────────
    # Stores the vector embedding for each chunk.
    # The VECTOR(1536) column is what pgvector adds — it stores a float array
    # that represents the semantic meaning of the text.
    print(f"\n📋 Creating table: chunk_embeddings (dim={EMBEDDING_DIM}) ...")
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS chunk_embeddings (
            id              SERIAL PRIMARY KEY,
            chunk_id        TEXT UNIQUE NOT NULL REFERENCES document_chunks(chunk_id),
            embedding       VECTOR({EMBEDDING_DIM}) NOT NULL,  -- the semantic vector
            model_name      TEXT NOT NULL,                      -- which model generated it
            created_at      TIMESTAMPTZ DEFAULT NOW()
        );
    """)

    # Create an IVFFlat index for approximate nearest-neighbour search.
    # This makes similarity search FAST even with thousands of chunks.
    # lists=100 is a good default for up to ~1M vectors.
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_chunk_embeddings_vector
        ON chunk_embeddings
        USING ivfflat (embedding vector_cosine_ops)
        WITH (lists = 100);
    """)
    print("✅ chunk_embeddings ready (with IVFFlat ANN index).")

    # ── 4. Table: chunk_node_links ──────────────────────────────────────────
    # This is the CORE of GraphRAG: it links a text chunk to graph nodes.
    # When the chatbot finds a relevant chunk, it can follow this link
    # to the graph to get richer structured context.
    print("\n📋 Creating table: chunk_node_links ...")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS chunk_node_links (
            id              SERIAL PRIMARY KEY,
            chunk_id        TEXT NOT NULL REFERENCES document_chunks(chunk_id),
            neptune_node_id TEXT NOT NULL,   -- matches node.id in Neptune, e.g. "node_engine"
            link_reason     TEXT,            -- why this chunk was linked to this node
            relevance_score FLOAT,           -- optional: 0.0 to 1.0 score from matching
            created_at      TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE(chunk_id, neptune_node_id)
        );
    """)
    print("✅ chunk_node_links ready.")

    # ── 5. Helpful view ─────────────────────────────────────────────────────
    # A view that joins everything for easy querying.
    print("\n📋 Creating view: v_chunk_full ...")
    cur.execute("""
        CREATE OR REPLACE VIEW v_chunk_full AS
        SELECT
            dc.chunk_id,
            dc.source_file,
            dc.page_number,
            dc.text_content,
            ce.embedding,
            ce.model_name,
            cnl.neptune_node_id,
            cnl.link_reason,
            cnl.relevance_score
        FROM document_chunks dc
        LEFT JOIN chunk_embeddings ce  ON dc.chunk_id = ce.chunk_id
        LEFT JOIN chunk_node_links cnl ON dc.chunk_id = cnl.chunk_id;
    """)
    print("✅ v_chunk_full view ready.")

    cur.close()
    conn.close()
    print("\n🎉 RDS PostgreSQL database fully configured for GraphRAG!")


if __name__ == "__main__":
    if "your-rds-instance" in DB_HOST:
        print("❌ ERROR: Please set RDS_HOST environment variable.")
        print("   Copy .env.example to .env and fill in your RDS details.")
    else:
        setup_database()
