# GTI Teams Bot (Agentic) & RS Alerts — Google Cloud Platform (GCP)

This folder contains the complete **Google Cloud Platform (GCP)** implementation of the **Google Threat Intelligence (GTI) Microsoft Teams Agentic Bot** and **RS Alerts** background alert processor.

Infrastructure is fully automated with **Terraform**, running on **Cloud Run functions (2nd Gen Cloud Functions)**, with **Google Cloud Storage (GCS)** for source code & manifest ZIP packaging, and **Google Cloud Firestore** for durable state & configuration persistence.

---

## Architecture

```
                                          ┌─────────────────────────────────────────┐
                                          │          Google Cloud Storage           │
                                          │  - gti-bot.zip & rs-alerts.zip (src)    │
                                          │  - teams-app-manifest.zip (sideloadable)│
                                          └─────────────────────────────────────────┘
                                                               ▲
                                                               │ Deploy source
┌─────────────────────────┐   POST /api/messages   ┌───────────┴─────────────┐   x-apikey   ┌────────────────────────┐
│  Microsoft Teams / Bot  │ ─────────────────────> │      Cloud Run fn       │ ───────────> │  GTI Agentic Sessions  │
│        Framework        │ <───────────────────── │        (gti-bot)        │ <─────────── │      (VirusTotal)      │
└─────────────────────────┘     Adaptive Cards     └───────────┬─────────────┘              └────────────────────────┘
                                                               │ Read/Seed
                                                               ▼
┌─────────────────────────┐      POST trigger      ┌─────────────────────────┐   Get alerts ┌────────────────────────┐
│  Google Cloud Scheduler │ ─────────────────────> │      Cloud Run fn       │ ───────────> │ GTI Threat Intelligence│
│    (cron: */15 * * * *) │      (OIDC Auth)       │       (rs-alerts)       │ <─────────── │       Alerts API       │
└─────────────────────────┘                        └───────────┬─────────────┘              └────────────────────────┘
                                                               │ Save Cursor
                                                               ▼
                                          ┌─────────────────────────────────────────┐
                                          │      Google Cloud Firestore Native      │
                                          │  - bot-config/output-format             │
                                          │  - rs-alerts-state/cursor               │
                                          └─────────────────────────────────────────┘
```

---

## Directory Structure

```
gcp/
├── gti-bot/                      # Interactive Microsoft Teams Bot (FastAPI + Cloud Run function)
│   ├── app/
│   │   ├── config.py             # Settings & GCP Firestore configuration
│   │   ├── constants.py          # App constants & prompt loader
│   │   ├── logging_config.py     # GCP Cloud Logging structured JSON formatter
│   │   ├── observability.py      # Cloud Trace header correlation
│   │   ├── output_format_store.py# Firestore persistence for output formatting instructions
│   │   ├── gti/prompt.md         # Threat intelligence system prompt
│   │   ├── gti/client.py         # Async client for GTI Agentic Sessions API
│   │   ├── teams/                # Teams SDK App, handlers, and Adaptive Card builders
│   │   └── utils/                # Message deliverer, card parser, and prompt formatters
│   ├── teams-app-manifest/       # Icons and manifest.json template
│   ├── main.py                   # FastAPI app + Functions Framework HTTP entrypoint
│   ├── requirements.txt          # Python dependencies
│   └── .env.example              # Sample environment variables
│
├── rs-alerts/                    # Background GTI Alerts -> Teams channel worker
│   ├── app/
│   │   ├── config.py             # Settings for RS Alerts & Firestore
│   │   ├── bot_auth.py           # Microsoft Entra ID client-credentials token manager
│   │   ├── cards.py              # Adaptive Card v1.4 builder for GTI alerts
│   │   ├── gti_client.py         # GTI List Alerts API client & token exchange
│   │   ├── job.py                # Alert fetch, filter, and Firestore checkpointing flow
│   │   ├── sender.py             # Bot Framework Connector API sender
│   │   └── state_store.py        # Firestore persistence for incremental cursor
│   ├── main.py                   # Cloud Run function entrypoint (invoked by Cloud Scheduler)
│   ├── requirements.txt          # Python dependencies
│   └── .env.example              # Sample environment variables
│
└── infra/                        # Complete Terraform Infrastructure as Code
    ├── main.tf                   # APIs, GCS, Firestore, Secrets, Cloud Run functions, IAM, Scheduler
    ├── variables.tf              # Input variables with validation & defaults
    ├── outputs.tf                # Function URLs, messaging endpoints, GCS URLs
    └── terraform.tfvars.example  # Example variable values
```

---

## Key Features & Highlights

1. **Storage Separation**:
   - **Google Cloud Storage (GCS)**: Stores function deployment packages (`gti-bot.zip`, `rs-alerts.zip`) and the generated sideloadable Teams app manifest ZIP (`teams-app-manifest.zip`).
   - **Google Cloud Firestore**: Stores durable bot formatting instructions (`bot-config/output-format`) and incremental alert cursor checkpoints (`rs-alerts-state/cursor`).

2. **Secret Management**:
   - Sensitive credentials (`GTI_API_KEY`, `CLIENT_SECRET`) are stored in **Google Secret Manager** and injected into the Cloud Run functions at runtime.

3. **Enterprise Security & Observability**:
   - Functions Framework HTTP entrypoint uses `a2wsgi` to serve the async FastAPI application.
   - Structured JSON logging natively indexed by **Google Cloud Logging**.
   - Per-request trace propagation matching `X-Cloud-Trace-Context` for **Google Cloud Trace**.

---

## 1. Local Setup & Authentication (Dual-Cloud)

This deployment automatically creates the GCP infrastructure, the Azure AD App Registration, and the Azure Bot Service. You must authenticate to both clouds locally before running Terraform.

If you are using **Google Cloud Shell**, the Azure CLI (`az`) is not installed by default. You can install it by running:
```bash
curl -sL https://aka.ms/InstallAzureCLIDeb | sudo bash
```

1. **Google Cloud Auth**:
   ```bash
   gcloud auth application-default login
   ```
2. **Azure Auth**:
   ```bash
   az login
   ```
   *(Note: You must have Application Administrator permissions in your Azure Active Directory to create the App Registration, and standard contributor permissions on your subscription to create the Bot Service).*

## 2. Infrastructure Deployment

### Prerequisites
- Google Cloud SDK (`gcloud`) authenticated.
- Terraform `v1.5.0+` installed.
- Google Threat Intelligence (VirusTotal) API key.

### Deployment with Terraform

1. Create a `terraform.tfvars` file:
   ```bash
   cd /home/devuser/gti-team-bot-agentic-azure/gcp/terraform
   cp terraform.tfvars.example terraform.tfvars
   ```

2. Edit `terraform.tfvars` with your credentials.

3. Deploy:
   ```bash
   terraform init
   terraform apply
   ```

4. Configure Microsoft Teams / Azure Bot:
   - Copy the output `gti_bot_messaging_endpoint` (e.g. `https://<region>-<project>.cloudfunctions.net/gti-team-bot/api/messages`).
   - Paste it into your Azure Bot Messaging Endpoint setting in the Azure Portal.
   - Download the generated Teams manifest zip from GCS (`teams_manifest_zip_gcs_url`) and sideload it into Microsoft Teams.
