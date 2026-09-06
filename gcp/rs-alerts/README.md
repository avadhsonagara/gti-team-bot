# RS Alerts (GTI Alerts -> Microsoft Teams) — GCP Cloud Run Functions

RS Alerts is a background worker that polls the **Google Threat Intelligence (GTI) List Alerts API** on a schedule and posts new alerts as Adaptive Cards directly to a Microsoft Teams channel via the **Bot Framework Connector API**.

---

## Architecture Overview

- **Compute**: Google Cloud Run functions (2nd Gen Cloud Functions) with Python 3.11/3.12 runtime.
- **Identity & Authentication**: Uses **Application Default Credentials (ADC)** and User-Assigned Managed Identity (User-Managed Service Account) for seamless access to Firestore, Secret Manager, Cloud Logging, and Cloud Trace without static key files.
- **Trigger**: Triggered periodically by **Google Cloud Scheduler** (via OIDC service account authentication) or manually via HTTP.
- **State & Checkpoint**: The incremental alert cursor (`last_update_time`) is saved in **Google Cloud Firestore** (Native mode, collection: `rs-alerts-state`, document: `cursor`).
- **Secrets Management**: Credentials (`GTI_API_KEY`, `CLIENT_SECRET`) are retrieved securely from **Google Secret Manager**.
- **Observability**: Structured JSON logging indexed by Google Cloud Logging.

---

## Configuration Settings

| Variable | Description | Default |
|---|---|---|
| `TEAMS_CHANNEL_ID` | Teams channel link or ID (`19:...@thread.tacv2`) | _Required_ |
| `GTI_API_KEY` | Google Threat Intelligence / VirusTotal API Key | _Required_ |
| `GTI_RSA_PROJECT` | GTI RSA Project ID (`projects/<id>` or `<id>`) | Defaults to `GCP_PROJECT_ID` |
| `CLIENT_ID` | Microsoft App ID (Client ID) from Entra ID App Registration | _Required_ |
| `CLIENT_SECRET` | Microsoft App Secret (Client Secret) from Entra ID App Registration | _Required_ |
| `TENANT_ID` | Microsoft Entra Tenant ID | _Required_ |

| `PAGE_SIZE` | Page size for GTI Alerts API pagination | `1000` |
| `FILTER_SEVERITY_LEVEL` | Allowed severities (comma-separated: `LOW,MEDIUM,HIGH`) | `MEDIUM,HIGH` |
| `FILTER_PRIORITY_LEVEL` | Allowed priorities (comma-separated: `LOW,MEDIUM,HIGH,CRITICAL`) | `MEDIUM,HIGH,CRITICAL` |
| `FILTER_RELEVANCE_LEVEL` | Allowed relevance levels (comma-separated: `LOW,MEDIUM,HIGH`) | `MEDIUM,HIGH` |
| `FILTER_RELEVANCE_CONFIDENCE` | Allowed confidence levels (comma-separated: `LOW,MEDIUM,HIGH`) | `MEDIUM,HIGH` |
| `GCP_PROJECT_ID` | Google Cloud Project ID | `gtimsteamaiintegration-3898` |
| `FIRESTORE_DATABASE` | Firestore Database ID | `(default)` |
| `FIRESTORE_STATE_COLLECTION` | Firestore collection for state storage | `rs-alerts-state` |
| `FIRESTORE_STATE_DOC` | Firestore document for alert cursor | `cursor` |

---

## Local Development & Manual Trigger

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

4. Run the job directly:
   ```bash
   python3 main.py
   ```
