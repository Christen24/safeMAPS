# SafeMAPS MCP — Cloud Run Deployment Readiness

## 1. Pre-Deployment Audit Results

| Component | Status | Notes |
|---|---|---|
| **Graph Ownership** | Ready | Phase 2 correctly isolated all graph processing to the MCP container. |
| **PORT Handling** | Ready | Updated `mcp_server.py` to respect the standard `$PORT` variable injected by Cloud Run, falling back to 8001. |
| **Host Binding** | Ready | Uvicorn binds to `0.0.0.0` securely. |
| **Health Endpoint** | Ready | `/health` returns HTTP 200 with JSON status. Compatible with Cloud Run health checks. |
| **Local Dependencies** | Ready | No hardcoded `safemaps-postgis` or `127.0.0.1` references in the MCP source code. Database connections read safely from `config.py` environment variables. |
| **Secrets/Credentials** | Ready | No secrets committed. Git `.gitignore` properly handles `.env`. |
| **Scheduler** | Ready | Scheduler starts automatically via the FastMCP wrapper's lifespan context in `mcp_server.py`. |

## 2. Infrastructure & Resources

Based on Phase 1/2 memory profiling (~755 MiB for graph loading):

*   **Memory:** `2 GiB` (Provides ~1.2 GiB headroom for concurrent request processing).
*   **CPU:** `2 vCPU` (Routing algorithms and PyTorch inference benefit from multi-threading).
*   **Concurrency:** `80` (Standard for FastAPI async workloads).
*   **Min Instances:** `1` (Graph initialization takes ~10-15s; keeping 1 instance warm prevents cold starts for end-users).
*   **Max Instances:** `3` (Cost control; scale up as traffic demands).

## 3. Environment Variables & Secrets Required

Provide these at deployment time to the Cloud Run service:

```ini
# --- Database (Supabase Session Pooler) ---
POSTGRES_HOST=aws-0-ap-south-1.pooler.supabase.com
POSTGRES_PORT=5432
POSTGRES_DB=postgres
POSTGRES_USER=postgres.khyrggnokfkwykonpprw
POSTGRES_PASSWORD=********          # (Provide as a Secret)
POSTGRES_SSLMODE=require

# --- Integrations ---
WAQI_API_TOKEN=********             # (Provide as a Secret)
TOMTOM_API_KEY=********             # (Provide as a Secret)
OPENROUTER_API_KEY=********         # (Provide as a Secret)
```

## 4. Exact Deployment Commands

To deploy the MCP service to Google Cloud Run (assuming Google Cloud CLI is authenticated):

**Step 1: Build and Push Docker Image**
```bash
# From the repository root (C:\Users\chris\OneDrive\Desktop\safeMAPS)
gcloud builds submit --tag gcr.io/YOUR_PROJECT_ID/safemaps-mcp -f backend/Dockerfile.mcp
```

**Step 2: Deploy to Cloud Run**
```bash
gcloud run deploy safemaps-mcp \
  --image gcr.io/YOUR_PROJECT_ID/safemaps-mcp \
  --platform managed \
  --region asia-south1 \
  --memory 2Gi \
  --cpu 2 \
  --min-instances 1 \
  --max-instances 3 \
  --allow-unauthenticated \
  --set-env-vars="POSTGRES_HOST=aws-0-ap-south-1.pooler.supabase.com,POSTGRES_PORT=5432,POSTGRES_DB=postgres,POSTGRES_USER=postgres.khyrggnokfkwykonpprw,POSTGRES_SSLMODE=require" \
  --set-secrets="POSTGRES_PASSWORD=safemaps-db-pass:latest,WAQI_API_TOKEN=safemaps-waqi-token:latest,TOMTOM_API_KEY=safemaps-tomtom-key:latest,OPENROUTER_API_KEY=safemaps-openrouter-key:latest"
```

*Note: Replace `YOUR_PROJECT_ID` with your GCP project ID and ensure the secrets exist in GCP Secret Manager.*

## 5. Deployment Blockers
**None.** The architecture is completely decoupled, production-configured, and verified against the live target database.
