"""
chunk_and_embed.py
------------------
STEP 2b – Chunk the PDF and generate vector embeddings, then store in RDS.

HOW RAG WORKS (simplified):
  1. You take your document (PDF) and split it into small pieces (chunks).
  2. Each chunk is converted into a vector embedding — a list of ~1536 numbers
     that captures the SEMANTIC MEANING of the text.
  3. You store these embeddings in a vector database (pgvector in our case).
  4. At query time, you embed the user's question the same way and find the
     chunks whose vectors are closest (cosine similarity) → those are the
     most relevant pieces of text to answer the question.

CHUNKING STRATEGY:
  We use a "recursive character text splitter" — it tries to split at paragraph
  boundaries first, then sentences, then words. This keeps context intact.
  chunk_size=512 tokens is a good default for technical documents.
  chunk_overlap=64 ensures context is not lost at boundaries.

EMBEDDING MODEL:
  By default we use Amazon Bedrock's Titan Embeddings (no extra cost beyond API calls,
  and it stays within AWS — important for data residency).
  Alternatively you can use OpenAI ada-002 by switching EMBEDDING_PROVIDER to "openai".

HOW TO RUN:
  python src/2_process_pdf/chunk_and_embed.py
"""

import os
import json
import uuid
import hashlib
import psycopg2
import boto3
from pathlib import Path
from typing import List, Tuple

import pdfplumber          # for reading PDFs
from langchain.text_splitter import RecursiveCharacterTextSplitter

# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------
PDF_PATH        = Path(__file__).parent.parent.parent / "data" / "power_plant_rds_pp_codes.pdf"
EMBEDDING_PROVIDER = os.getenv("EMBEDDING_PROVIDER", "bedrock")   # "bedrock" or "openai"
AWS_REGION      = os.getenv("AWS_REGION", "us-east-1")

# RDS connection
DB_HOST         = os.getenv("RDS_HOST",     "your-rds.xxxx.us-east-1.rds.amazonaws.com")
DB_PORT         = os.getenv("RDS_PORT",     "5432")
DB_NAME         = os.getenv("RDS_DB_NAME",  "graphrag")
DB_USER         = os.getenv("RDS_USER",     "graphrag_admin")
DB_PASSWORD     = os.getenv("RDS_PASSWORD", "changeme")

CHUNK_SIZE      = int(os.getenv("CHUNK_SIZE",    "512"))
CHUNK_OVERLAP   = int(os.getenv("CHUNK_OVERLAP", "64"))


# ---------------------------------------------------------------------------
# EMBEDDING
# ---------------------------------------------------------------------------

class BedrockEmbedder:
    """
    Uses Amazon Bedrock's Titan Embed Text model to create embeddings.
    Bedrock is AWS's managed AI model service — no model to host yourself.
    Make sure your IAM role has the 'AmazonBedrockFullAccess' policy.
    """
    MODEL_ID = "amazon.titan-embed-text-v2:0"
    DIM = 1024

    def __init__(self):
        self.client = boto3.client("bedrock-runtime", region_name=AWS_REGION)

    def embed(self, text: str) -> List[float]:
        body = json.dumps({"inputText": text})
        response = self.client.invoke_model(
            modelId=self.MODEL_ID,
            body=body,
            accept="application/json",
            contentType="application/json",
        )
        result = json.loads(response["body"].read())
        return result["embedding"]

    @property
    def model_name(self):
        return self.MODEL_ID


class OpenAIEmbedder:
    """
    Uses OpenAI's text-embedding-ada-002 model.
    Requires OPENAI_API_KEY environment variable.
    """
    MODEL_ID = "text-embedding-ada-002"
    DIM = 1536

    def __init__(self):
        import openai
        self.client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

    def embed(self, text: str) -> List[float]:
        response = self.client.embeddings.create(input=text, model=self.MODEL_ID)
        return response.data[0].embedding

    @property
    def model_name(self):
        return self.MODEL_ID


def get_embedder():
    if EMBEDDING_PROVIDER == "bedrock":
        return BedrockEmbedder()
    elif EMBEDDING_PROVIDER == "openai":
        return OpenAIEmbedder()
    else:
        raise ValueError(f"Unknown EMBEDDING_PROVIDER: {EMBEDDING_PROVIDER}")


# ---------------------------------------------------------------------------
# PDF CHUNKING
# ---------------------------------------------------------------------------

def extract_text_from_pdf(pdf_path: Path) -> List[Tuple[int, str]]:
    """
    Extract text page by page from the PDF.
    Returns a list of (page_number, text) tuples.
    pdfplumber handles both normal text and tables well.
    """
    print(f"📄 Extracting text from: {pdf_path.name}")
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for i, page in enumerate(pdf.pages, start=1):
            # Extract regular text
            text = page.extract_text() or ""
            # Also extract tables and append as structured text
            for table in page.extract_tables():
                for row in table:
                    cleaned = [cell or "" for cell in row]
                    text += "\n" + " | ".join(cleaned)
            if text.strip():
                pages.append((i, text.strip()))
    print(f"  Extracted {len(pages)} pages with content.")
    return pages


def chunk_pages(pages: List[Tuple[int, str]]) -> List[dict]:
    """
    Split page text into overlapping chunks using LangChain's splitter.
    Each chunk dict has: chunk_id, source_file, page_number, chunk_index, text_content.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    all_chunks = []
    for page_num, text in pages:
        sub_chunks = splitter.split_text(text)
        for idx, chunk_text in enumerate(sub_chunks):
            # Generate a stable, unique ID based on content
            chunk_hash = hashlib.md5(chunk_text.encode()).hexdigest()[:12]
            chunk_id = f"chunk_{page_num:03d}_{idx:03d}_{chunk_hash}"
            all_chunks.append({
                "chunk_id":     chunk_id,
                "source_file":  PDF_PATH.name,
                "page_number":  page_num,
                "chunk_index":  idx,
                "text_content": chunk_text,
                "char_count":   len(chunk_text),
            })

    print(f"  Created {len(all_chunks)} chunks (size≤{CHUNK_SIZE}, overlap={CHUNK_OVERLAP}).")
    return all_chunks


# ---------------------------------------------------------------------------
# DATABASE STORAGE
# ---------------------------------------------------------------------------

def get_db_connection():
    return psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
        user=DB_USER, password=DB_PASSWORD
    )


def insert_chunk(cur, chunk: dict):
    """Insert a text chunk into document_chunks table (skip duplicates)."""
    cur.execute("""
        INSERT INTO document_chunks (chunk_id, source_file, page_number, chunk_index, text_content, char_count)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (chunk_id) DO NOTHING;
    """, (
        chunk["chunk_id"], chunk["source_file"], chunk["page_number"],
        chunk["chunk_index"], chunk["text_content"], chunk["char_count"]
    ))


def insert_embedding(cur, chunk_id: str, embedding: List[float], model_name: str):
    """
    Insert the embedding vector into chunk_embeddings.
    pgvector expects the vector as a string like '[0.1, 0.2, ...]'
    """
    vector_str = "[" + ",".join(str(x) for x in embedding) + "]"
    cur.execute("""
        INSERT INTO chunk_embeddings (chunk_id, embedding, model_name)
        VALUES (%s, %s::vector, %s)
        ON CONFLICT (chunk_id) DO UPDATE SET embedding = EXCLUDED.embedding;
    """, (chunk_id, vector_str, model_name))


# ---------------------------------------------------------------------------
# MAIN PIPELINE
# ---------------------------------------------------------------------------

def main():
    if not PDF_PATH.exists():
        print(f"❌ PDF not found at {PDF_PATH}")
        print("   Run: python data/generate_pdf.py  first.")
        return

    # 1. Extract text
    pages = extract_text_from_pdf(PDF_PATH)

    # 2. Chunk
    chunks = chunk_pages(pages)

    # 3. Connect to embedder and DB
    print(f"\n🤖 Loading embedder: {EMBEDDING_PROVIDER}")
    embedder = get_embedder()
    print(f"✅ Embedder ready: {embedder.model_name}")

    print(f"\n🔌 Connecting to RDS at {DB_HOST}...")
    conn = get_db_connection()
    cur  = conn.cursor()
    print("✅ Connected to RDS.")

    # 4. Process each chunk
    print(f"\n⚙️  Processing {len(chunks)} chunks...")
    for i, chunk in enumerate(chunks, 1):
        chunk_id = chunk["chunk_id"]
        text     = chunk["text_content"]

        # Store raw chunk
        insert_chunk(cur, chunk)

        # Generate embedding (API call to Bedrock or OpenAI)
        embedding = embedder.embed(text)

        # Store embedding
        insert_embedding(cur, chunk_id, embedding, embedder.model_name)

        conn.commit()   # commit after each chunk so we don't lose progress on error

        if i % 10 == 0 or i == len(chunks):
            print(f"  [{i}/{len(chunks)}] ✓ chunk {chunk_id} | {chunk['char_count']} chars")

    cur.close()
    conn.close()
    print(f"\n🎉 Done! {len(chunks)} chunks embedded and stored in RDS pgvector.")


if __name__ == "__main__":
    main()
