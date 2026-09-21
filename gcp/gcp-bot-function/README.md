# GTI Teams Bot (Agentic) — GCP Cloud Run function

This is the **Google Threat Intelligence (GTI) Microsoft Teams Agentic Bot**,
packaged as a **Cloud Run function (2nd Gen Cloud Functions)**.

---

## Architecture

- **Compute**: Google Cloud Run functions (2nd Gen), Python 3.12, invoked via
  [Functions Framework](https://github.com/GoogleCloudFunctions/functions-framework-python)
  (`main.gti_bot_http`), running on Flask/WSGI.
- **Fully synchronous, no Teams SDK.** Bot Framework auth (`app/teams/auth.py`),
  Activity parsing (`app/teams/activity.py`), messaging
  (`app/teams/bot_client.py` / `app/teams/context.py`), the GTI/Graph clients,
  and Firestore access are all plain synchronous code using `requests` /
  `firestore.Client` — no `microsoft-teams-apps` SDK, no async runtime.
- **State & Custom Instructions**: Google Cloud Firestore (collection:
  `gti-bot-config`).
- **Secrets**: `GTI_API_KEY` / `CLIENT_SECRET` should be injected via Secret
  Manager at deploy time.
- **Observability**: Structured JSON logging on GCP (Cloud Logging) via
  `app/logging_config.py`/`app/observability.py`.

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

See `.env.example` for the full list with defaults. The required ones:

| Variable | Description |
|---|---|
| `CLIENT_ID` | Microsoft App ID (Client ID) from Entra ID App Registration |
| `CLIENT_SECRET` | Microsoft App Secret (Client Secret) from Entra ID App Registration |
| `TENANT_ID` | Microsoft Entra Tenant ID |
| `GTI_API_KEY` | Google Threat Intelligence / VirusTotal API Key |

---

## Local Development

1. Create a virtual environment and install dependencies:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Set up Application Default Credentials (ADC) for local Firestore access:
   ```bash
   gcloud auth application-default login
   ```

3. Copy `.env.example` to `.env` and fill in credentials:
   ```bash
   cp .env.example .env
   ```

4. Run locally via Functions Framework:
   ```bash
   functions-framework --target=gti_bot_http --port=8080
   ```

5. Expose locally via ngrok or Cloudflare Tunnel:
   ```bash
   ngrok http 8080
   ```
   Set the messaging endpoint in your Azure Bot registration to
   `https://<your-ngrok-domain>/api/messages`.

---

## Deploying

```bash
gcloud run deploy gti-bot-function \
  --source=. \
  --function=gti_bot_http \
  --region=<region> \
  --allow-unauthenticated \
  --set-env-vars=CLIENT_ID=...,TENANT_ID=...,GCP_PROJECT_ID=... \
  --set-secrets=CLIENT_SECRET=client-secret:latest,GTI_API_KEY=gti-api-key:latest
```

(Adjust to your project's existing Terraform/deploy tooling under
`../terraform` if you wire this function into that setup instead.)
