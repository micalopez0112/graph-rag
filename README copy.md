# ⚡ Power Plant GraphRAG on AWS

A complete, beginner-friendly GraphRAG system using:

- **AWS Neptune** – graph database (components & relationships)
- **AWS RDS PostgreSQL + pgvector** – vector database (PDF embeddings)
- **Amazon Bedrock** – LLM (Claude 3) + Embeddings (Titan)

---

## What is GraphRAG and why is it useful here?

**Plain RAG (Retrieval-Augmented Generation):** You split a document into chunks, store their vector embeddings, and at query time you find the most similar chunks and feed them to an LLM.

**GraphRAG adds the graph:** When a relevant chunk is found, the system also fetches the surrounding _graph structure_ — what components are connected, what controls what, what the relationships are. This means:

> _"What fails if the AVR breaks?"_
>
> Plain RAG only finds text about the AVR. GraphRAG _also_ traverses the graph: AVR → controls excitation of → Alternator → feeds → Main Breaker → etc. The LLM gets both the text **and** the structured relationships → much better answer.

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                          YOUR VPC (private network)                      │
│                                                                          │
│   ┌─────────────────┐      Gremlin/WSS      ┌──────────────────────┐    │
│   │  Your App /     │ ─────────────────────▶ │   AWS Neptune         │   │
│   │  EC2 / Cloud9   │                        │   (Graph DB)          │   │
│   │                 │ ─────────────────────▶ │   nodes + edges       │   │
│   │                 │      psycopg2          └──────────────────────┘    │
│   │                 │ ─────────────────────▶ ┌──────────────────────┐    │
│   └────────┬────────┘                        │  AWS RDS PostgreSQL   │   │
│            │                                 │  + pgvector           │   │
│            │                                 │  (Vector DB)          │   │
│            │                                 └──────────────────────┘    │
│            │ HTTPS                                                        │
└────────────┼─────────────────────────────────────────────────────────────┘
             ▼
   ┌──────────────────────┐
   │  Amazon Bedrock      │
   │  - Titan Embeddings  │
   │  - Claude 3 Sonnet   │
   └──────────────────────┘
```

---

## Folder Structure

```
graph-rag/
├── data/
│   ├── power_plant_graph.json      ← The graph (nodes + edges)
│   ├── generate_pdf.py             ← Script to generate the PDF
│   └── power_plant_rds_pp_codes.pdf  ← Generated PDF (after running the script)
│
├── src/
│   ├── 1_upload_graph/
│   │   └── upload_to_neptune.py    ← Translates JSON graph → Gremlin → Neptune
│   │
│   ├── 2_process_pdf/
│   │   ├── setup_rds_pgvector.py   ← Creates tables + pgvector in RDS
│   │   └── chunk_and_embed.py      ← Chunks PDF + creates embeddings → RDS
│   │
│   ├── 3_associate_chunks/
│   │   └── associate_chunks_to_nodes.py  ← Links chunks to graph nodes
│   │
│   └── 4_chatbot/
│       ├── graphrag_retriever.py   ← The retrieval engine (core logic)
│       └── chatbot.py              ← CLI + HTTP API chatbot
│
├── requirements.txt
├── env.example                     ← Copy to .env and fill in your values
└── README.md
```

---

## Step-by-Step AWS Setup (for beginners)

### Prerequisites on your local machine

```bash
# Install Python 3.11+
python3 --version

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate    # on Mac/Linux
# .venv\Scripts\activate     # on Windows

# Install all dependencies
pip install -r requirements.txt
```

---

### STEP 0 – Set up your AWS account

1. Go to [https://aws.amazon.com](https://aws.amazon.com) and sign in.
2. Go to **IAM** (Identity & Access Management) in the console.
3. Create a new **IAM User** (don't use the root account for daily work):
   - Name it `graphrag-developer`
   - Attach these **managed policies** (AWS pre-built permission sets):
     - `AmazonNeptuneFullAccess`
     - `AmazonRDSFullAccess`
     - `AmazonBedrockFullAccess`
     - `AmazonVPCFullAccess` (needed to configure networking)
   - Under **Security credentials**, create an **Access Key** (type: CLI).
   - Download the `.csv` — these are your credentials.

4. Install AWS CLI:

   ```bash
   # Mac
   brew install awscli
   # or: pip install awscli

   aws configure
   # Enter: Access Key ID, Secret Access Key, Region (us-east-1), Output (json)
   ```

5. Verify it works:
   ```bash
   aws sts get-caller-identity
   # Should print your account ID
   ```

---

### STEP 1 – Enable Amazon Bedrock models (Deprecated)

> **Why?** Bedrock is AWS's AI model service. You need to explicitly "enable" the models you want to use — they are not on by default.

1. Go to **AWS Console → Bedrock → Model access** (left sidebar).
2. Click **Enable specific models**.
3. Enable:
   - `Amazon → Titan Embeddings V1` (for creating embeddings)
   - `Anthropic → Claude 3 Sonnet` (for the LLM chat)
4. Wait a few minutes for approval (usually instant for these models).

---

### STEP 2 – Create a VPC (private network)

> **Why?** Neptune and RDS must live inside a private network (VPC) so they are not exposed to the internet. Your Cloud9 dev environment will also live here. We use **public subnets only** to avoid the $32/month NAT Gateway cost — this is fine for development.

1. Go to **AWS Console → VPC → Create VPC**.
2. Choose **VPC and more** (creates subnets automatically).
3. Settings:
   - Name: `graphrag-vpc`
   - IPv4 CIDR: `10.0.0.0/16`
   - Availability Zones: **2**
   - Public subnets: **2**
   - Private subnets: **0** ← set this to zero
   - NAT Gateway: **None** ← no cost
   - VPC Endpoints: **None**
4. Click **Create VPC**.

> **💡 Why no private subnets?** Private subnets require a NAT Gateway (~$32/month) to reach the internet. Since Neptune and RDS security groups only allow traffic from inside the VPC, putting them in a public subnet does **not** mean they are publicly accessible — their security groups block all outside traffic. Cloud9 needs to be in a public subnet anyway so your browser can reach it.

---

### STEP 3 – Create an AWS Neptune Cluster

> **Why Neptune?** It is a managed graph database that supports Gremlin queries. "Managed" means AWS handles backups, patching, and failover automatically.

1. Go to **AWS Console → Neptune → Create database**.
2. Choose:
   - Engine version: **1.3.x** (latest)
   - Templates: **Dev/Test** (cheaper for learning)
   - DB cluster identifier: `graphrag-neptune`
   - Instance class: `db.t3.medium` (cheapest option, fine for our data size)
   - VPC: `graphrag-vpc`
   - Subnet group: Create new → select **both public subnets**
   - VPC security group: Create new → `neptune-sg`
     - Inbound rule: TCP port **8182**, Source: **Custom** → `10.0.0.0/16` (VPC CIDR — only machines inside the VPC can connect)
3. Click **Create database**.
4. Wait ~10 minutes for it to be available.
5. Note the **Writer endpoint** — it looks like: `graphrag-neptune.cluster-xxxx.us-east-1.neptune.amazonaws.com`

> ⚠️ Even though Neptune is in a public subnet, the security group only allows port 8182 from within the VPC (`10.0.0.0/16`). It is **not** reachable from the internet.

> **💡 Cost estimate:** `db.t3.medium` = ~$0.065/hour = ~$47/month. **Stop or delete it when not using it during development.**

---

### STEP 4 – Create an RDS PostgreSQL Instance

> **Why RDS?** It is AWS's managed PostgreSQL. We will enable the `pgvector` extension on it to store our vector embeddings.

1. Go to **AWS Console → RDS → Create database**.
2. Choose:
   - Engine: **PostgreSQL** version **15.x** or **16.x**
   - Templates: **Free tier** (yes, RDS has a free tier! `db.t3.micro`, 20GB)
   - DB instance identifier: `graphrag-rds`
   - Master username: `graphrag_admin`
   - Master password: choose a strong password, save it
   - Instance class: `db.t3.micro` (free tier eligible)
   - Storage: 20 GB (free tier)
   - VPC: `graphrag-vpc`
   - Subnet group: select **both public subnets** (same ones as Neptune)
   - Public access: **No** — even in a public subnet, this keeps RDS from getting a public IP
   - VPC security group: Create new → `rds-sg`
     - Inbound rule: TCP port **5432**, Source: **Custom** → `10.0.0.0/16` (VPC CIDR only)
   - Initial database name: `graphrag`
3. Click **Create database**.
4. Wait ~5 minutes.
5. Note the **Endpoint** under Connectivity.

> **💡 Cost:** Free tier for 12 months for `db.t3.micro`. After that ~$0.017/hour.

---

### STEP 5 – Create a Cloud9 IDE (your dev environment inside the VPC)

> **Why Cloud9?** Both Neptune and RDS are inside a private VPC — they have no public IP. Cloud9 is an AWS browser-based IDE that runs on an EC2 instance _inside_ the VPC, so it can reach both databases directly. No SSH tunnel needed.

1. Go to **AWS Console → Cloud9 → Create environment**.
2. Settings:
   - Name: `graphrag-dev`
   - Instance type: `t3.small` (enough for our scripts)
   - Platform: Amazon Linux 2023
   - **Network settings → VPC**: `graphrag-vpc`
   - **Network settings → Subnet**: pick either of the two **public** subnets (they will be named something like `graphrag-vpc-subnet-public1-us-east-1a`)
3. Click **Create**.
4. Open the environment (takes ~1 minute to start).
5. In the Cloud9 terminal, clone this repo:
   ```bash
   git clone https://github.com/YOUR_USERNAME/graph-rag.git
   cd graph-rag
   pip install -r requirements.txt
   ```
6. Copy the env file and fill it in:
   ```bash
   cp env.example .env
   nano .env    # fill in your Neptune endpoint, RDS host, etc.
   ```
7. Load the env vars:
   ```bash
   export $(cat .env | grep -v '^#' | xargs)
   ```

> **💡 Why a public subnet for Cloud9?** Cloud9 needs a public IP so your browser can connect to it. It can still reach Neptune and RDS because they are all in the same VPC (`10.0.0.0/16`). Cloud9 automatically **stops** the EC2 when you close the browser tab, so you only pay when actively using it. `t3.small` = ~$0.023/hour.

---

### STEP 6 – Generate the data files

Run these on your local machine OR in Cloud9:

```bash
# Generate the PDF
python data/generate_pdf.py
# → creates: data/power_plant_rds_pp_codes.pdf
```

The `data/power_plant_graph.json` is already included in the repo.

---

### STEP 7 – Upload the graph to Neptune

> **What this does:** Reads `power_plant_graph.json`, translates each node into a Gremlin `addV()` command and each edge into a Gremlin `addE()` command, then sends them to Neptune.

```bash
# Run from Cloud9 (inside the VPC)
python src/1_upload_graph/upload_to_neptune.py
```

Expected output:

```
📂 Loaded graph: 'Small Diesel Power Plant' with 10 nodes and 15 edges.
🔌 Connecting to Neptune...
✅ Connected to Neptune.
🗑️  Clearing existing graph data...
📤 Uploading 10 nodes...
  ➕ Adding vertex: [DieselEngine] id=node_engine ...
  ...
📤 Uploading 15 edges...
  ...
🔍 Verifying upload...
  Graph contains: 10 vertices, 15 edges
🎉 Graph successfully uploaded to AWS Neptune!
```

You can also verify in the **Neptune console** → **Query editor** (Gremlin):

```groovy
g.V().valueMap(true).limit(5)
```

---

### STEP 8 – Set up RDS and create the vector tables

> **What this does:** Connects to your RDS PostgreSQL instance, enables the `pgvector` extension, and creates the tables for storing chunks and embeddings.

```bash
python src/2_process_pdf/setup_rds_pgvector.py
```

Then chunk the PDF and generate embeddings:

```bash
python src/2_process_pdf/chunk_and_embed.py
```

> ⚠️ This makes API calls to Amazon Bedrock for each chunk. With ~50 chunks from our PDF, the cost is fractions of a cent.

---

### STEP 9 – Associate PDF chunks to graph nodes

> **What this does:** For each text chunk, finds which Neptune graph nodes it talks about (by looking for PP codes like "PP-ENG-001" and by semantic similarity). Stores the links in RDS.

```bash
python src/3_associate_chunks/associate_chunks_to_nodes.py
```

---

### STEP 10 – Run the chatbot

```bash
# Interactive CLI chatbot
python src/4_chatbot/chatbot.py

# OR: Run as HTTP API (e.g. to connect a frontend)
python src/4_chatbot/chatbot.py --server
# Then POST to: http://localhost:8080/ask
# Body: { "question": "What should I do if fault F-002 triggers?" }
```

---

## Example Chatbot Interaction

```
You: What happens when the AVR fails?

Bot: When the Automatic Voltage Regulator (PP-AVR-001) fails, fault code F-009
(AVR Fault) or F-010 (Excitation Loss) will be triggered on the Generator
Control Panel (PP-GCP-001).

The immediate impact is on the Synchronous Alternator (PP-ALT-001): without
proper excitation control, the alternator output voltage will become unstable
or collapse. The generator will likely experience undervoltage (F-007) shortly
after, which may cause the Main Generator Circuit Breaker (PP-MCB-001) to trip
if voltage falls below 360V.

Required actions:
1. Check for fault F-009 or F-010 on PP-GCP-001.
2. If available, switch to manual excitation control.
3. If not, the recommended action is to replace the AVR (model: Stamford MX341)
   or contact the manufacturer.
4. Do not attempt to re-energise until the AVR is confirmed functional.

Maintenance code M-011 (AVR setpoint verification, annual) should be performed
to prevent recurrence.
```

---

## Cost Estimate (with $100 AWS credits)

| Service                    | Config                        | Estimated Cost      |
| -------------------------- | ----------------------------- | ------------------- |
| Neptune                    | `db.t3.medium`, used 20h/week | ~$6/month           |
| RDS PostgreSQL             | `db.t3.micro` (free tier)     | $0/month (1st year) |
| Cloud9/EC2                 | `t3.small`, used 20h/week     | ~$2/month           |
| Bedrock (Titan Embeddings) | ~50 chunks × $0.0001          | < $0.01 per run     |
| Bedrock (Claude 3 Sonnet)  | ~100 questions                | < $1/month          |
| NAT Gateway                | **None (not used)**           | **$0**              |

> **Tip:** Stop or delete the Neptune cluster when you are not actively developing — it is the biggest cost item. You can stop it from the Neptune console in one click and restart it later; your data is preserved.

> **Tip:** With this setup (no NAT Gateway, RDS on free tier, Cloud9 used only when needed), your $100 credits should comfortably last **3–4 months** of active development.

---

## Quick Reference: Key AWS Services Used

| Service            | What it is                  | Why we use it                                                    |
| ------------------ | --------------------------- | ---------------------------------------------------------------- |
| **Neptune**        | Managed graph database      | Stores components and their relationships in a traversable graph |
| **RDS PostgreSQL** | Managed relational database | Stores PDF text chunks and vector embeddings via pgvector        |
| **Bedrock**        | Managed AI model API        | Generates embeddings (Titan) and chat answers (Claude)           |
| **Cloud9**         | Browser-based IDE on EC2    | Runs inside the VPC so it can reach Neptune and RDS privately    |
| **VPC**            | Private network             | Isolates Neptune/RDS from the internet                           |
| **IAM**            | Identity & permissions      | Controls which AWS services can call which other services        |

---

## Troubleshooting

**`Connection refused` to Neptune:**
→ You are not inside the VPC. Use Cloud9, or set up an SSH tunnel from your laptop.

**`ssl: certificate verify failed` with Neptune:**
→ Neptune uses WSS (WebSocket Secure). Make sure your endpoint starts with `wss://`.

**`pgvector extension not found`:**
→ Make sure you are using PostgreSQL 15+ on RDS. Enable the extension: `CREATE EXTENSION vector;`

**Bedrock `AccessDeniedException`:**
→ You need to enable the model in Bedrock console first (see STEP 1). Also check IAM permissions.

**`no space left on device` in Cloud9:**
→ Cloud9 `t2.micro` has 10GB. Resize: EC2 → Volumes → Modify → increase to 20GB.
