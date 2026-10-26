# Google Cloud Run & Docker Deployment Guide: `metrotickets`

This guide provides end-to-end instructions for containerizing and deploying the **`metrotickets`** multi-agent Bureau on **Google Cloud Run** using Docker and Fetch.ai Agentverse Mailbox mode.

---

## 1. Architecture Overview

```
                                  GOOGLE CLOUD RUN ARCHITECTURE
 ┌─────────────────────────────────────────────────────────────────────────────────────────────┐
 │                                                                                             │
 │                               Google Cloud Project                                         │
 │                                                                                             │
 │   ┌──────────────────────┐                     ┌────────────────────────────────────────┐  │
 │   │ Google Secret        │                     │ Cloud Run Service: "metrotickets"     │  │
 │   │ Manager              │                     │                                        │  │
 │   │                      │                     │  ┌──────────────────────────────────┐  │  │
 │   │ • AGENTVERSE_KEY     │───(Env Injection)──>│  │ Python 3.12 Container (Port 8080)│  │  │
 │   │ • ASI_ONE_API_KEY    │                     │  │                                  │  │  │
 │   │ • COORDINATOR_SEED   │                     │  │   uAgents Multi-Agent Bureau     │  │  │
 │   │ • ONDC_KEYS          │                     │  │   ├── metro_bap_coordinator     │  │  │
 │   └──────────────────────┘                     │  │   ├── metro_bpp_catalog_agent    │  │  │
 │                                                │  │   └── metro_bpp_ticketing_agent  │  │  │
 │   ┌──────────────────────┐                     │  │                                  │  │  │
 │   │ Artifact Registry    │───(Image Deploy)───>│  │   Serving:                       │  │  │
 │   │ asia-south1-docker   │                     │  │   • HTTP :8080 (Health / QR API) │  │  │
 │   │ /metrotickets:latest │                     │  │   • Outbound WSS: Agentverse     │  │  │
 │   └──────────────────────┘                     │  └──────────────────────────────────┘  │  │
 │                                                └──────────────────┬─────────────────────┘  │
 └───────────────────────────────────────────────────────────────────┼────────────────────────┘
                                                                     │
                                             Outbound WSS / TLS      │  Inbound Messages
                                            (ENABLE_MAILBOX=true)    │  Routed via Mailbox
                                                                     ▼
                                                  ┌──────────────────────────────────────┐
                                                  │       Fetch.ai Agentverse            │
                                                  │         Mailbox Relay                │
                                                  └──────────────────┬───────────────────┘
                                                                     │
                                                                     ▼
                                                  ┌──────────────────────────────────────┐
                                                  │         ASI:One Chat Network         │
                                                  │       Rider: @metrotickets           │
                                                  └──────────────────────────────────────┘
```

### Why Mailbox Mode for Cloud Run?
In standard webhook mode, agents require a permanent public HTTPS ingress URL registered on the Fetch.ai Almanac. On Google Cloud Run:
- Dynamic IP reassignment and cold starts can disrupt incoming webhooks.
- Inbound firewall and ingress routing require custom DNS configurations.
- By enabling **Agentverse Mailbox Mode** (`ENABLE_MAILBOX=true`), the coordinator establishes an authenticated outbound connection to the Agentverse mailbox cluster.
- Agentverse queues incoming chat messages from riders and delivers them instantly over the persistent connection. No public ingress or tunnels (e.g. ngrok) are required.

---

## 2. Prerequisites

1. **Google Cloud SDK (`gcloud`)** installed and authenticated:
   ```bash
   gcloud auth login
   gcloud auth application-default login
   ```
2. **Active Google Cloud Project** with billing enabled:
   ```bash
   export GCP_PROJECT_ID="your-project-id"
   export GCP_REGION="asia-south1" # Mumbai region for minimal latency to DMRC APIs
   gcloud config set project $GCP_PROJECT_ID
   ```
3. **Required APIs Enabled**:
   ```bash
   gcloud services enable \
     run.googleapis.com \
     artifactregistry.googleapis.com \
     secretmanager.googleapis.com \
     cloudbuild.googleapis.com
   ```
4. **Fetch.ai Agentverse Account**: An API key from [Agentverse Profile Settings](https://agentverse.ai/).
5. **ASI:One Developer Key**: An API key from [api.asi1.ai](https://api.asi1.ai/).

---

## 3. Docker Container Configuration

The system includes a production-tuned `Dockerfile` optimized for Python 3.12 and asynchronous uAgents I/O:

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p tickets/dev tickets/test tickets/prod

EXPOSE 8080

CMD ["python", "metro_booking_agents.py"]
```

### Key Container Features:
- **`PORT=8080`**: Defaults to the standard Cloud Run container port. The ASGIServer in `metro_booking_agents.py` reads `os.environ.get("PORT", "8000")`.
- **System SSL Bundles**: Installs `ca-certificates` to support secure TLS handshakes with Fetch.ai Dorado nodes, Agentverse, and OTD Delhi APIs.
- **ALMANAC Timeout Tuning**: uAgents Almanac registration timeout is configured to 15.0s in `common/config.py` to prevent TLS handshake timeouts in containerized cloud environments.

---

## 4. Step-by-Step Deployment to Google Cloud Run

### Step 1: Create Artifact Registry Repository
Create a Docker registry repository in your target region:
```bash
gcloud artifacts repositories create metro-containers \
  --repository-format=docker \
  --location=$GCP_REGION \
  --description="Docker repository for metrotickets multi-agent bureau"
```

Configure Docker authentication for the repository:
```bash
gcloud auth configure-docker ${GCP_REGION}-docker.pkg.dev
```

### Step 2: Store Secrets in Google Secret Manager
Store sensitive credentials in Secret Manager to avoid baking secrets into Docker layers or environment files:

```bash
# 1. Agentverse User Key
echo -n "your_agentverse_token" | gcloud secrets create METROTICKETS_AGENTVERSE_KEY \
  --data-file=- \
  --replication-policy="automatic"

# 2. ASI:One LLM API Key
echo -n "your_asi_one_api_key" | gcloud secrets create METROTICKETS_ASI_ONE_KEY \
  --data-file=- \
  --replication-policy="automatic"

# 3. Agent Seed Phrases (Generated via python scripts/generate_production_keys.py)
echo -n "your_private_coordinator_seed" | gcloud secrets create METROTICKETS_COORDINATOR_SEED \
  --data-file=- \
  --replication-policy="automatic"

# 4. Delhi OTD API Key
echo -n "your_otd_delhi_api_key" | gcloud secrets create METROTICKETS_OTD_KEY \
  --data-file=- \
  --replication-policy="automatic"
```

### Step 3: Build and Push Docker Image
Use Google Cloud Build to build and push the container directly from the root repository:

```bash
export IMAGE_URI="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/metro-containers/metrotickets:v1.0.0"

gcloud builds submit --tag $IMAGE_URI .
```

### Step 4: Configure Cloud Run Service Account
Create a dedicated service account with minimal privileges:
```bash
gcloud iam service-accounts create metrotickets-runner \
  --display-name="MetroTickets Cloud Run Service Account"

# Grant permission to read required secrets
gcloud secrets add-iam-policy-binding METROTICKETS_AGENTVERSE_KEY \
  --member="serviceAccount:metrotickets-runner@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding METROTICKETS_ASI_ONE_KEY \
  --member="serviceAccount:metrotickets-runner@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding METROTICKETS_COORDINATOR_SEED \
  --member="serviceAccount:metrotickets-runner@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

gcloud secrets add-iam-policy-binding METROTICKETS_OTD_KEY \
  --member="serviceAccount:metrotickets-runner@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"
```

### Step 5: Deploy Service to Cloud Run

Deploy the service named `metrotickets`:

```bash
gcloud run deploy metrotickets \
  --image=$IMAGE_URI \
  --region=$GCP_REGION \
  --platform=managed \
  --service-account="metrotickets-runner@${GCP_PROJECT_ID}.iam.gserviceaccount.com" \
  --allow-unauthenticated \
  --port=8080 \
  --memory=1Gi \
  --cpu=1 \
  --min-instances=1 \
  --max-instances=5 \
  --timeout=3600 \
  --set-env-vars="APP_ENV=dev,ENABLE_MAILBOX=true,AGENT_HANDLE=metrotickets,AGENT_NAME=Delhi Metro Ticket Booking" \
  --set-secrets="AGENTVERSE_KEY=METROTICKETS_AGENTVERSE_KEY:latest,ASI_ONE_API_KEY=METROTICKETS_ASI_ONE_KEY:latest,COORDINATOR_SEED=METROTICKETS_COORDINATOR_SEED:latest,OTD_DELHI_API_KEY=METROTICKETS_OTD_KEY:latest"
```

> [!IMPORTANT]
> **Why `--min-instances=1` is critical**:
> When using Agentverse Mailbox mode, an agent maintains a long-lived outbound WebSocket connection. If Cloud Run scales to zero during idle periods, the agent drops off the mailbox until an external HTTP request arrives. Setting `--min-instances=1` ensures the agent process remains active 24/7 to receive real-time messages from ASI:One chat riders at all times.

---

## 5. Verifying the Deployment

### 1. Inspect Service Status & Logs
Check that all three agents started and registered their mailbox successfully:
```bash
gcloud run services describe metrotickets --region=$GCP_REGION
```

Stream live application logs:
```bash
gcloud beta run services logs tail metrotickets --region=$GCP_REGION
```

Expected log output:
```text
INFO: [metro_bap_coordinator]: Starting agent with address: agent1q...
INFO: [metro_bpp_catalog_agent]: Starting agent with address: agent1q...
INFO: [metro_bpp_ticketing_agent]: Starting agent with address: agent1q...
INFO: [bureau]: Starting server on http://0.0.0.0:8080
[MAILBOX] Auto-provisioned Agentverse mailbox for Delhi Metro Ticket Booking (agent1q...)
INFO: [metro_bap_coordinator]: Manifest published successfully: AgentChatProtocol
INFO: [metro_bap_coordinator]: Agent registration status updated to active
```

### 2. Verify HTTP & QR Health Endpoints
Get the Cloud Run service public URL:
```bash
SERVICE_URL=$(gcloud run services describe metrotickets --region=$GCP_REGION --format="value(status.url)")
echo "Service URL: $SERVICE_URL"
```

Test station graph API:
```bash
curl -s "${SERVICE_URL}/metro/stations" | python3 -m json.tool | head -n 25
```

---

## 6. One-Time Agentverse Almanac Link

If you need to manually bind or refresh your Mailbox agent in the Agentverse web console:
1. Navigate to [Agentverse](https://agentverse.ai/) and log in.
2. Locate the agent listing under **My Agents** corresponding to your coordinator address.
3. Verify the agent state shows **Connected (Mailbox)** with an active green indicator.
4. Set the Chat Name to `metrotickets` and ensure the public readme is uploaded from `agent_readme.md`.
5. In ASI:One (`https://asi1.ai/chat`), riders can now interact directly with `@metrotickets`.

---

## 7. Cloud Run Continuous Updates

When updates are pushed to the repository:
```bash
# Rebuild image with new version tag
export NEW_IMAGE="${GCP_REGION}-docker.pkg.dev/${GCP_PROJECT_ID}/metro-containers/metrotickets:v1.1.0"
gcloud builds submit --tag $NEW_IMAGE .

# Deploy new revision
gcloud run deploy metrotickets \
  --image=$NEW_IMAGE \
  --region=$GCP_REGION
```
Cloud Run automatically performs zero-downtime rolling deployment of the new container revision.
