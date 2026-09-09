# GTI Teams Bot (Agentic) — GCP Cloud Run function

This is the **Google Threat Intelligence (GTI) Microsoft Teams Agentic Bot**,
packaged as a **Cloud Run function (2nd Gen Cloud Functions)**, replacing the previous
SDK-based bot implementation.

---

## Architecture

- **Compute**: Google Cloud Run functions (2nd Gen), Python 3.11 runtime,
  invoked via [Functions Framework](https://github.com/GoogleCloudFunctions/functions-framework-python)
  (`main.gti_bot_http`). Functions Framework runs on Flask/WSGI: it calls one
  plain Python function per HTTP request — there is no persistent ASGI
  server or event loop shared across requests.
- **No Microsoft Teams SDK, no async, anywhere.**
  This version has no dependency on `microsoft-teams-apps` SDK (which is async-only
  internally and caused event loop closed issues on warm instances).
  Instead, this function talks to Bot Framework directly with
  plain `requests`, the same style used by [`../rs-alerts`](../rs-alerts):
  - `app/teams/auth.py` — verifies the inbound JWT (RS256 against Bot
    Framework's public JWKS: issuer, audience, expiry, and a `serviceUrl` claim match).
  - `app/teams/activity.py` — parses the raw Activity JSON into the same
    attribute shape the SDK's typed model gave the handlers, so that
    business logic didn't need to change.
  - `app/teams/bot_client.py` / `app/teams/context.py` — the bot's own
    Connector API token, and sending/editing/deleting a Teams message.
- **Everything else is synchronous too** — the GTI client, Graph client,
  Firestore session/output-format stores, and thread-context fetching are
  all plain `def`s making blocking `requests`/`firestore.Client` calls
  directly.
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
