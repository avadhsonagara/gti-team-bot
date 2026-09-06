# GTI Teams Bot (Agentic) — GCP Cloud Run Functions

This is the **Google Threat Intelligence (GTI) Microsoft Teams Agentic Bot** running natively on **Google Cloud Platform (GCP)** using **Cloud Run functions (2nd Gen Cloud Functions)**.

---

## Architecture Overview

- **Compute**: Google Cloud Run functions (2nd Gen) with Python 3.11 runtime, wrapping the FastAPI ASGI application via `a2wsgi` and Functions Framework.
- **Identity & Authentication**: Uses **Application Default Credentials (ADC)** and User-Assigned Managed Identity (User-Managed Service Account) for seamless access to Firestore, Secret Manager, Cloud Logging, and Cloud Trace without static key files.
- **Messaging Endpoint**: Exposes `/api/messages` to receive inbound activities from Microsoft Teams / Bot Framework.
- **State & Custom Instructions**: Persisted durably in **Google Cloud Firestore** (Native mode, collection: `bot-config`, document: `output-format`).
- **Secrets Management**: Sensitive values (`GTI_API_KEY`, `CLIENT_SECRET`) are secured via **Google Secret Manager** and injected into the function at runtime.
- **Observability**: Structured JSON logging natively indexed by Google Cloud Logging, with trace correlation via `X-Cloud-Trace-Context` headers.

---

## Endpoints

| Route | Method | Purpose |
|---|---|---|
| `/` | `GET` | Service liveness & metadata |
| `/health` | `GET` | Health check probe |
| `/api/messages` | `POST` | Microsoft Teams Bot Framework webhook endpoint |
| `/api/messages` | `OPTIONS` | CORS / preflight handler |
| `/api/messages` | `GET` | Informational status endpoint |

---

## Configuration Settings

| Variable | Description | Default |
|---|---|---|
| `CLIENT_ID` | Microsoft App ID (Client ID) from Entra ID App Registration | _Required_ |
| `CLIENT_SECRET` | Microsoft App Secret (Client Secret) from Entra ID App Registration | _Required_ |
| `TENANT_ID` | Microsoft Entra Tenant ID | _Required_ |

| `GTI_API_KEY` | Google Threat Intelligence / VirusTotal API Key | _Required_ |
| `GTI_API_BASE_URL` | GTI API base URL | `https://www.virustotal.com/api/v3` |
| `GCP_PROJECT_ID` | Google Cloud Project ID | `gtimsteamaiintegration-3898` |
| `FIRESTORE_DATABASE` | Firestore Database ID | `(default)` |
| `FIRESTORE_BOT_CONFIG_COLLECTION` | Firestore collection for bot config | `bot-config` |
| `FIRESTORE_OUTPUT_FORMAT_DOC` | Firestore document for output format | `output-format` |
| `OUTPUT_FORMAT_INSTRUCTIONS` | Deploy-time default formatting instructions (seeds Firestore) | `""` |

---

## Local Development

1. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Set up Application Default Credentials (ADC) for local testing:
   ```bash
   gcloud auth application-default login
   ```

3. Copy `.env.example` to `.env` and fill in credentials:
   ```bash
   cp .env.example .env
   ```

4. Run locally:
   ```bash
   python3 main.py
   # Or using Functions Framework:
   functions-framework --target=gti_bot_http --port=8080
   ```

5. Expose locally via ngrok or Cloudflare Tunnel:
   ```bash
   ngrok http 8080
   ```
   Set the messaging endpoint in your Azure Bot registration to `https://<your-ngrok-domain>/api/messages`.
