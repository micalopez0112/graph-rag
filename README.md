# ⚡ Power Plant GraphRAG on AWS

A complete, beginner-friendly GraphRAG system using:

- **Neo4j** – graph database running locally in Docker (free, no AWS cost)
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
   ┌──────────────────────────────────────────────────────────────┐
   │              EC2 dev instance (inside AWS VPC)               │
   │                                                              │
   │  ┌─────────────────────┐   Bolt/7687   ┌─────────────────┐  │
   │  │  Python Scripts /   │ ────────────▶ │  Neo4j (Docker) │  │
   │  │  Chatbot            │               │  Graph DB       │  │
   │  │                     │   psycopg2    └─────────────────┘  │
   │  │                     │ ────────────▶ ┌─────────────────┐  │
   │  └──────────┬──────────┘               │  AWS RDS        │  │
   │             │ HTTPS                    │  PostgreSQL     │  │
   └─────────────┼────────────────────────  │  + pgvector     │  │
                 │                          └─────────────────┘  │
                 ▼                          (inside same VPC)     │
   ┌──────────────────────┐                └──────────────────────┘
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
│   │   └── upload_to_neo4j.py      ← Translates JSON graph → Cypher → Neo4j
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

> **Why?** RDS must live inside a private network (VPC) so it is not exposed to the internet. Your EC2 instance (which also runs Neo4j in Docker) will also live here. We use **public subnets only** to avoid the $32/month NAT Gateway cost — this is fine for development.

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

> **💡 Why no private subnets?** Private subnets require a NAT Gateway (~$32/month) to reach the internet. RDS security groups only allow traffic from inside the VPC, so putting it in a public subnet does **not** make it publicly accessible — the security group blocks all outside traffic. The EC2 instance needs to be in a public subnet so SSM Session Manager can reach it.

---

---

### STEP 3 – Create an RDS PostgreSQL Instance

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
   - Subnet group: select **both public subnets**
   - Public access: **No** — even in a public subnet, this keeps RDS from getting a public IP
   - VPC security group: Create new → `rds-sg`
     - Inbound rule: TCP port **5432**, Source: **Custom** → `10.0.0.0/16` (VPC CIDR only)
   - Initial database name: `graphrag`
3. Click **Create database**.
4. Wait ~5 minutes.
5. Note the **Endpoint** under Connectivity.

> **💡 Cost:** Free tier for 12 months for `db.t3.micro`. After that ~$0.017/hour.

---

### STEP 4 – Launch an EC2 instance (your dev environment inside the VPC)

> **Why EC2 + SSM instead of Cloud9?** AWS Cloud9 was discontinued for new accounts in 2024. The replacement is a plain EC2 instance accessed through **AWS Systems Manager (SSM) Session Manager**, which gives you a browser terminal with no SSH keys needed and no extra cost.
> RDS is inside the VPC, Neo4j runs in Docker on the same EC2 instance, and your Python scripts run there too — everything in one place.

#### STEP 4a – Create an IAM role for the EC2

> The EC2 needs permissions to reach RDS, Bedrock, and SSM (for the browser terminal).

1. **AWS Console → IAM → Roles → Create role**
2. Trusted entity type: **AWS service → EC2**
3. Attach these managed policies:
   - `AmazonSSMManagedInstanceCore` ← enables the browser terminal
   - `AmazonRDSFullAccess`
   - `AmazonBedrockFullAccess`
4. Name it `graphrag-ec2-role` → **Create role**

#### STEP 4b – Launch the EC2 instance

1. **AWS Console → EC2 → Launch instance**
2. Settings:
   - Name: `graphrag-dev`
   - AMI: **Amazon Linux 2023** (free tier eligible)
   - Instance type: `t3.small` (~$0.023/h) — or `t2.micro` (free tier) if you want zero cost
   - Key pair: **Proceed without a key pair** — you will use SSM instead
   - Network settings:
     - VPC: `graphrag-vpc`
     - Subnet: either of the two **public** subnets (named like `graphrag-vpc-subnet-public1-us-east-1a`)
     - Auto-assign public IP: **Enable**
     - Security group: Create new → `ec2-sg`
       - **Add no inbound rules** — SSM works via outbound HTTPS only, so no ports need to be open ✅
   - Advanced details → **IAM instance profile**: `graphrag-ec2-role`
3. Click **Launch instance**. Wait ~1 minute.

#### STEP 4c – Open a browser terminal (SSM Session Manager)

1. **AWS Console → EC2 → Instances** → select `graphrag-dev`
2. Click **Connect** (top-right button)
3. Choose the **Session Manager** tab
4. Click **Connect** — a terminal opens in your browser ✅

#### STEP 4d – Set up the environment (run once)

```bash
# Install Python 3.11, pip, git
sudo dnf install -y python3.11 python3.11-pip git

# Install Docker (to run Neo4j)
sudo dnf install -y docker
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ssm-user

# Apply docker group without logging out
newgrp docker

# Clone your repo
git clone https://github.com/YOUR_USERNAME/graph-rag.git
cd graph-rag

# Create a virtual environment and install dependencies
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Copy the env file and fill it in
cp env.example .env
nano .env   # fill in RDS_HOST, AWS_REGION, and other values

# Load env vars into the shell
export $(cat .env | grep -v '^#' | xargs)
```

#### STEP 4e – Start Neo4j in Docker

```bash
docker run \
  --name neo4j-graphrag \
  --detach \
  --restart unless-stopped \
  -p 7474:7474 \
  -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/graphrag123 \
  -v $HOME/neo4j/data:/data \
  neo4j:5
```

Verify it started:
```bash
docker ps                                  # should show neo4j-graphrag as "Up"
docker logs neo4j-graphrag | tail -5       # should end with "Started"
```

#### Resuming work after stopping the EC2

```bash
# Start the instance: AWS Console → EC2 → select instance → Instance state → Start
# Then reconnect via Session Manager and run:
cd graph-rag
source .venv/bin/activate
export $(cat .env | grep -v '^#' | xargs)
# Neo4j restarts automatically (--restart unless-stopped)
```

> **💡 Cost:** EC2 `t3.small` = ~$0.023/hour. **Stop the instance** (not terminate) when not working — data is preserved, you only pay for the 20GB EBS volume (~$0.08/GB/month = ~$1.60/month while stopped). `t2.micro` is free tier eligible for the first 12 months.

---

### STEP 5 – Start Neo4j in Docker

> You now have an EC2 instance running (STEP 4). Connect to it via Session Manager and run the following commands in that terminal.

**Install Docker:**
```bash
sudo dnf install -y docker
sudo systemctl start docker
sudo systemctl enable docker
sudo usermod -aG docker ssm-user
newgrp docker          # apply group without logging out
docker --version       # verify
```

**Start Neo4j as a Docker container:**
```bash
docker run \
  --name neo4j-graphrag \
  --detach \
  --restart unless-stopped \
  -p 7474:7474 \
  -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/graphrag123 \
  -v $HOME/neo4j/data:/data \
  neo4j:5
```

What each flag does:
- `--detach` → runs in the background
- `--restart unless-stopped` → auto-starts if the EC2 reboots
- `-p 7687:7687` → exposes the Bolt port (what our Python code uses)
- `-p 7474:7474` → exposes the browser UI port (optional, for visual exploration)
- `-e NEO4J_AUTH=neo4j/graphrag123` → sets the username/password
- `-v $HOME/neo4j/data:/data` → persists graph data outside the container so it survives restarts

**Verify it's running:**
```bash
docker ps                               # should show neo4j-graphrag as "Up"
docker logs neo4j-graphrag | tail -5    # should end with "Started"
```

> **💡 Cost:** $0. Docker and Neo4j Community are completely free.

---

### STEP 6 – Generate the data files

Run these from the EC2 terminal (Session Manager) after cloning the repo:

```bash
# Generate the PDF
python data/generate_pdf.py
# → creates: data/power_plant_rds_pp_codes.pdf
```

The `data/power_plant_graph.json` is already included in the repo.

---

### STEP 7 – Upload the graph to Neo4j

> **What this does:** Reads `power_plant_graph.json`, translates each node into a Cypher `MERGE (n:Label {props})` statement and each edge into a `MATCH ... MERGE (a)-[:LABEL]->(b)` statement, then sends them to Neo4j.

```bash
# Run from EC2 (Neo4j is running in Docker on the same machine)
python src/1_upload_graph/upload_to_neo4j.py
```

Expected output:

```
📂 Loaded graph: 'Small Diesel Power Plant' — 10 nodes, 15 edges.
🔌 Connecting to Neo4j at bolt://localhost:7687...
✅ Connected to Neo4j.
🗑️  Clearing existing graph data...
📤 Uploading 10 nodes...
  ➕ [DieselEngine] node_engine — Diesel Prime Mover
  ...
📤 Uploading 15 edges...
  ➕ [MECHANICALLY_COUPLED] node_engine ──▶ node_alternator
  ...
🔍 Verification: 10 nodes, 15 relationships in Neo4j.
�� Graph successfully uploaded to Neo4j!
```

You can also explore the graph visually via the Cypher query in the Neo4j browser (if you set up the SSH tunnel):

```cypher
MATCH (n)-[r]->(m) RETURN n, r, m
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

> **What this does:** For each text chunk, finds which Neo4j graph nodes it talks about (by looking for PP codes like "PP-ENG-001" and by semantic similarity). Stores the links in RDS.

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
| Neo4j (Docker)             | Community edition             | **$0**              |
| RDS PostgreSQL             | `db.t3.micro` (free tier)     | $0/month (1st year) |
| EC2 (dev server)           | `t3.small`, used 20h/week     | ~$2/month           |
| Bedrock (Titan Embeddings) | ~50 chunks × $0.0001          | < $0.01 per run     |
| Bedrock (Claude 3 Sonnet)  | ~100 questions                | < $1/month          |
| NAT Gateway                | **None (not used)**           | **$0**              |

> **Tip:** With this setup (no NAT Gateway, RDS on free tier, EC2 stopped when not working), your $100 credits should comfortably last **3–4 months** of active development.

---

## Quick Reference: Key AWS Services Used

| Service            | What it is                  | Why we use it                                                    |
| ------------------ | --------------------------- | ---------------------------------------------------------------- |
| **Neo4j**          | Graph database (Docker)     | Stores components and their relationships in a traversable graph |
| **RDS PostgreSQL** | Managed relational database | Stores PDF text chunks and vector embeddings via pgvector        |
| **Bedrock**        | Managed AI model API        | Generates embeddings (Titan) and chat answers (Claude)           |
| **EC2 + SSM**      | EC2 with browser terminal   | Runs Neo4j (Docker) and scripts inside the VPC alongside RDS     |
| **VPC**            | Private network             | Isolates RDS from the internet; EC2 and Neo4j live here too      |
| **IAM**            | Identity & permissions      | Controls which AWS services can call which other services        |

---

## Troubleshooting

**`Connection refused` to Neo4j:**
→ Check that the Docker container is running: `docker ps`. If not: `docker start neo4j-graphrag`.
→ Check logs: `docker logs neo4j-graphrag | tail -20`

**`Authentication failed` on Neo4j:**
→ Make sure `NEO4J_PASSWORD=graphrag123` in your `.env` matches the `-e NEO4J_AUTH=neo4j/graphrag123` flag used when starting the container.

**`pgvector extension not found`:**
→ Make sure you are using PostgreSQL 15+ on RDS. Enable the extension: `CREATE EXTENSION vector;`

**Bedrock `AccessDeniedException`:**
→ You need to enable the model in Bedrock console first (see STEP 1). Also check IAM permissions.

**`no space left on device` on EC2:**
→ The default EBS volume is 8GB. Resize it: AWS Console → EC2 → Volumes → select the volume → **Modify volume** → increase to 20GB → confirm. Then run `sudo growpart /dev/xvda 1 && sudo xfs_growfs /` on the instance.
