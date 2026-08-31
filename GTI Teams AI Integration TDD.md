# GTI Microsoft Teams AI Integration

Technical Design Document

**Status:** Approach B (Azure Native) is the primary target architecture. Approach A (GCP) is documented as the reference architecture for Google Cloud environments.

---

## Table of Contents

1. [Overview](#overview)
2. [Technical Requirements](#technical-requirements)
3. [Deployment Architecture & Strategy](#deployment-architecture--strategy)
4. [Security & Data Privacy](#security--data-privacy)
5. [System Architecture — Two Approaches](#system-architecture--two-approaches)
6. [Deep Dive: The Bot Backend (FastAPI + ASGI)](#deep-dive-the-bot-backend-fastapi--asgi)
7. [Deep Dive: GTI Agentic Sessions API](#deep-dive-gti-agentic-sessions-api)
8. [Deep Dive: Scalability, Resilience, & State Management](#deep-dive-scalability-resilience--state-management)
9. [Deployment Instructions](#deployment-instructions)
10. [User Perspective & Interaction Examples](#user-perspective--interaction-examples)
11. [References](#references)

---

## Overview

The **Google Threat Intelligence (GTI) Microsoft Teams Integration** is a smart assistant designed for security operations teams. It allows security analysts to look up cyber threats and analyze suspicious indicators directly within Microsoft Teams — including 1:1 direct chats, group discussions, and alert channels.

### How It Works in Simple Terms

In daily operations, security analysts frequently need to check indicators like suspicious IP addresses, domain names, file hashes, CVE vulnerabilities, or threat actor groups. Instead of leaving Teams to open separate web portals or command-line tools, analysts can simply ask the bot inside Teams.

The bot acts as a lightweight, secure connector between Microsoft Teams and Google's hosted **GTI Agentic Sessions API**:
1. **User asks a question:** An analyst asks a question in Teams (for example: `@GTI What is known about IP address 8.8.8.8?`).
2. **Bot forwards the query:** The bot immediately forwards the user's question directly to Google's hosted threat intelligence agent.
3. **Google's agent investigates:** Google's hosted agent processes the request, orchestrates lookups across Google Threat Intelligence data sources, and determines the threat verdict.
4. **Interactive response in Teams:** The bot receives the formatted result and renders it in Teams as an interactive, easy-to-read Adaptive Card with risk scores, key evidence, and links to the full GTI console.

---

## Technical Requirements

### Functional Requirements

- **Direct Messages (1:1):** The bot is always active in direct messages. Users can ask questions directly without tagging the bot.
- **Group Chats & Channels:** In shared group chats and team channels, the bot responds whenever it is mentioned (`@GTI`).
- **Helpful User Guidance:** If a user sends an empty or unsupported message, the bot returns clear instructions and example prompts.
- **Immediate Progress Feedback:** When an analyst submits a query, the bot immediately posts a temporary message (`⏳ Looking into that with Google Threat Intelligence…`). Once the investigation finishes, this message is automatically replaced with the final results card.
- **Structured Response Cards:** Answers are formatted using Microsoft Teams Adaptive Cards, presenting key facts, severity indicators, and clickable links to the full Google Threat Intelligence web portal.

### Non-Functional Requirements

- **Security & Secret Management:** The bot only requires a single sensitive credential: the GTI API Key. This key is stored securely in Azure Key Vault (or GCP Secret Manager) and is never kept in plain text.
- **Performance & Timeouts:** Requests are processed synchronously with built-in retries and exponential backoff to handle temporary network delays smoothly.
- **Serverless Scalability:** The backend runs on serverless cloud compute (Azure Functions or GCP Cloud Run). It automatically scales on demand to handle multiple analyst queries at the same time and costs nothing when idle.
- **Resilience & Clear Error Handling:** If an error occurs (such as an invalid API key, network timeout, or rate limit), the bot catches the issue and displays a clear, friendly explanation in Teams instead of failing silently.

---

## Deployment Architecture & Strategy

A production Teams bot consists of three main components:

```
Microsoft Teams Users  ──►  Azure Bot Service (Secure Proxy)  ──►  Bot Backend (Azure Functions / Cloud Run)  ──►  GTI Agentic API
```

1. **Microsoft Teams App Package:** A zip package containing the application manifest and icons that a Teams administrator uploads to the organization's Teams admin center.
2. **Azure Bot Service:** A managed Microsoft service that acts as a secure communication bridge between Microsoft Teams and the bot backend. Every Microsoft Teams bot uses this service to securely route messages.
3. **Bot Backend:** A serverless web application that receives messages from Teams, forwards them to Google Threat Intelligence, and returns formatted visual cards.

---

## Security & Data Privacy

We intentionally do **not** publish this bot as a public, multi-tenant app on the Microsoft Teams Marketplace. By distributing this as Infrastructure-as-Code (Terraform/Bicep), we ensure the highest level of enterprise security:

- **Total Data Sovereignty:** A public marketplace app would require a single shared backend server that holds every customer's GTI API keys and processes all internal security queries in one shared environment. By deploying into **your own cloud subscription** (Azure or GCP), all API keys, chat messages, and threat queries remain entirely inside your security boundary.
- **Zero-Trust Identity:** The architecture leans heavily on identity-based authentication rather than static passwords. In Azure, the bot authenticates to the Bot Service and Key Vault using a **User-Assigned Managed Identity**. This eliminates the risk of leaked Client Secrets entirely.
- **Secret Vaulting:** The only static secret required is the `GTI_API_KEY`. It is never stored in environment variables directly; it is vaulted in **Azure Key Vault** or **GCP Secret Manager** and injected securely into the application memory only at runtime.

---

## System Architecture — Two Approaches

### Approach A: GCP Hosting (Reference Architecture)

For organizations hosting their workloads in Google Cloud:

```
┌───────────────────────────────────────────────────────┐
│                 Microsoft Teams                       │
└───────────────────────────────────────────────────────┘
            │                               ▲
    (1:1 &  │     Azure Bot Service         │ Bot
    Groups) ▼     (Bot Framework API)       │ API
┌───────────────────────┐       ┌───────────────────────┐
│    GCP Cloud Run      │       │    GCP Cloud Run      │
│    (Bot Backend)      │       │   (Alert Poller)      │
│                       │       │                       │
│ 1. Parse Message      │       │ 1. Cloud Scheduler    │
│ 2. Load Secrets       │       │ 2. Load Checkpoint    │
│    (Secret Manager)   │       │    (Firestore)        │
│ 3. Query GTI API      │       │ 3. Fetch/Filter Alerts│
│ 4. Build Adaptive Card│       │ 4. Send Adaptive Card │
│ 5. Reply to Teams     │       │ 5. Save Checkpoint    │
└───────────────────────┘       └───────────────────────┘
            │                               │
            ▼                               ▼
    GTI Agentic API                GTI Agentic API
  (sessions endpoint)             (alerts endpoint)
```

- **Compute:** GCP Cloud Run runs the interactive bot backend as an auto-scaling container service.
- **Secrets Management:** The GTI API key and Microsoft Client Secret are stored securely in **GCP Secret Manager** and provided to the application securely at startup.
- **State Storage:** **Firestore** stores configuration settings and alert tracking cursors.
- **Infrastructure as Code:** Automated with Terraform templates.

**Limitations:**
- **Cross-Cloud Dependency:** Demands infrastructure in both Azure (Bot Service, Entra ID) and GCP (Cloud Run). This requires managing Client Secrets to authenticate across clouds, rather than utilizing passwordless identities.
- **Deployment Complexity:** The Terraform module must authenticate to both clouds simultaneously (`gcloud auth` and `az login`) to provision the setup end-to-end, requiring the deployer to have cross-cloud administrative permissions.

---

### Approach B: Azure Native Hosting (Target Architecture)

For organizations whose primary cloud ecosystem is Microsoft Azure:

```
┌───────────────────────────────────────────────────────┐
│                 Microsoft Teams                       │
└───────────────────────────────────────────────────────┘
            │                               ▲
    (1:1 &  │     Azure Bot Service         │ Bot
    Groups) ▼    (Passwordless Auth)        │ API
┌───────────────────────┐       ┌───────────────────────┐
│ Azure Functions (HTTP)│       │Azure Functions (Timer)│
│    (Bot Backend)      │       │   (Alert Poller)      │
│                       │       │                       │
│ 1. Parse Message      │       │ 1. Scheduled Trigger  │
│ 2. Load Secrets       │       │ 2. Load Checkpoint    │
│    (Azure Key Vault)  │       │    (Blob Storage)     │
│ 3. Query GTI API      │       │ 3. Fetch/Filter Alerts│
│ 4. Build Adaptive Card│       │ 4. Send Adaptive Card │
│ 5. Reply to Teams     │       │ 5. Save Checkpoint    │
└───────────────────────┘       └───────────────────────┘
            │                               │
            ▼                               ▼
    GTI Agentic API                GTI Agentic API
  (sessions endpoint)             (alerts endpoint)
```

- **Compute:** **Azure Functions (Flex Consumption, Python)** hosts the bot application. It scales on demand and executes requests rapidly with zero idle infrastructure cost.
- **Passwordless Authentication:** The bot uses a **User-Assigned Managed Identity**. This allows Azure Bot Service and Azure Functions to securely authenticate with each other without creating, managing, or rotating client passwords.
- **Secret Protection:** The `GTI_API_KEY` is stored in **Azure Key Vault**. Azure Functions reads the key securely at runtime using its Managed Identity with the Key Vault Secrets User role.
- **Blob Storage:** An **Azure Storage Account** stores the deployment package, the generated Teams app manifest archive, and alert tracking checkpoints.
- **Monitoring & Observability:** **Application Insights** automatically collects performance metrics, error rates, and end-to-end request tracking IDs.
- **Automated Infrastructure:** Full setup is automated using Azure Bicep templates, supporting single-command deployments.

**Limitations:**
- **Platform Lock-in:** The architecture is tightly coupled to Azure-specific features (e.g., Managed Identities, Key Vault references, Flex Consumption scaling models, and Application Insights).
- **Deployment Speed:** Azure Functions Flex Consumption deployments (which rely on remote build processes and zip deployments) can take longer to build and start compared to standard containerized deployments like GCP Cloud Run.

---

### Cloud Service Mapping Matrix

| Functional Area | Google Cloud Platform (GCP) | Microsoft Azure |
| :--- | :--- | :--- |
| **Bot Backend Compute** | GCP Cloud Run (Service) | Azure Functions (Flex Consumption, Python) |
| **Scheduled Alert Worker** | GCP Cloud Run (Function) + Cloud Scheduler | Azure Functions Timer Trigger |
| **App Manifest Storage** | Google Cloud Storage (GCS) | Azure Blob Storage |
| **Alert Tracking Storage** | Google Cloud Firestore | Azure Blob Storage |
| **Secret Management** | GCP Secret Manager | Azure Key Vault + Managed Identity |
| **Bot Identity** | Microsoft Entra ID App Registration | User-Assigned Managed Identity |
| **Threat Intelligence Engine** | GTI Agentic Sessions API (Hosted) | GTI Agentic Sessions API (Hosted) |
| **Logs & Monitoring** | Cloud Logging & Cloud Trace | Azure Application Insights |
| **Infrastructure Automation** | Terraform | Azure Bicep Templates |

---

## Deep Dive: The Bot Backend (FastAPI + ASGI)

The bot backend is built using a modern web framework (**FastAPI**) rather than relying solely on the legacy Bot Framework SDK routers. This provides massive benefits for local testing, Pydantic validation, and OpenAPI documentation.

### The ASGI Portability Layer
To ensure the bot is cloud-agnostic, the core `main.py` FastAPI app is wrapped dynamically depending on the deployment target:
- **Locally:** It runs natively using `uvicorn`.
- **In GCP:** It uses the `a2wsgi` middleware to convert the ASGI app into a WSGI app compatible with Google Cloud's `functions-framework`.
- **In Azure:** It uses `azure.functions.AsgiFunctionApp`, allowing the exact same API router to map perfectly to the Azure Functions runtime without rewriting any HTTP logic.

### Lifespan and Connection Pooling
Instead of creating a new HTTP connection to the GTI API on every single Teams message, the application leverages FastAPI's `@asynccontextmanager lifespan` protocol. When the serverless container starts, it creates a persistent `httpx.AsyncClient` pool. When the container scales down, it shuts down the pool cleanly, drastically reducing TCP handshake latency.

---

## Deep Dive: GTI Agentic Sessions API

The bot does not run its own complex local language model. Instead, it delegates reasoning and threat intelligence data aggregation directly to Google's hosted **GTI Agentic Sessions API** via an `x-apikey` authentication header.

### Stateful Conversational Agent
Unlike traditional REST APIs where you query an IP and get a JSON dump, the `/agentspace/sessions/{session_id}` endpoint initiates a stateful session with an LLM. 
The LLM acts autonomously: it takes the user's prompt (e.g., "Analyze this domain"), identifies which internal VirusTotal or GTI tools to use, calls them, aggregates the intelligence, and summarizes it. 

### System Prompt & Formatting
To ensure the LLM returns data that fits perfectly inside a Microsoft Teams Adaptive Card, the bot injects a hidden **System Prompt** along with the user's query. This instructs the agent to:
1. Avoid generic pleasantries.
2. Structure the findings using standard Markdown (which Adaptive Cards parse).
3. Return a definitive threat verdict (Malicious, Suspicious, Safe).

The bot then extracts this markdown stream and renders it inside an interactive visual card.

---

## Deep Dive: Scalability, Resilience, & State Management

### Handling Transient Errors
The `gti_client.py` uses `tenacity` to implement exponential backoff. If the GTI API returns a `429 Too Many Requests` or a temporary `5xx` error, the bot will automatically sleep and retry up to `gti_max_retries` times. This ensures temporary network blips do not result in a broken experience for the security analyst in Teams.

### Serverless Concurrency Models
- **GCP Cloud Run:** Handles hundreds of concurrent requests per container instance. It is extremely fast to scale and highly efficient for high-volume chat environments.
- **Azure Flex Consumption:** Azure Functions dynamically allocates workers based on queue depth. By utilizing the new "Flex" consumption model, the bot benefits from faster virtual networking and reduced cold-start latency compared to traditional Azure Consumption plans.

### RS Alerts Checkpoint Persistence
The **Real-time System (RS) Alerting** workflow runs on a schedule (e.g., every 15 minutes) to poll GTI for new threat notifications. 
To guarantee alerts are never sent twice, it maintains a strict `updateTime` cursor:
- It fetches the cursor from **Firestore** (GCP) or **Azure Blob Storage** (Azure).
- It queries the GTI Alerts API for events newer than the cursor.
- It processes the events, sends them to the Teams Bot Framework API, and then writes the newest timestamp back to the storage layer atomically.

---

## Deployment Instructions

### Option 1: GCP Hosting (Reference)

#### Configuration Parameters (`terraform.tfvars`)

| Variable | Required | Default | Description |
| :--- | :---: | :--- | :--- |
| `project_id` | ✅ | | Your Google Cloud Project ID. |
| `region` | ✅ | | The Google Cloud region to deploy to (e.g., `us-central1`). |
| `gti_api_key` | ✅ | | Your Google Threat Intelligence API key. |
| `azure_tenant_id` | ✅ | | The Microsoft Entra ID Tenant ID for bot registration. |
| `azure_subscription_id` | ✅ | | The Microsoft Azure Subscription ID. |
| `rs_alerts_enabled` | | `false` | Set to `true` to deploy the Real-time System (RS) Alert poller. |
| `rs_alerts_teams_channel_id` | | | The Teams channel URL/ID for RS Alerts (`19:...@thread.tacv2`). |
| `rsa_schedule` | | `"*/15 * * * *"` | The Cloud Scheduler chron string for RS Alerts. |
| `rsa_filter_severity_level` | | `"MEDIUM,HIGH"` | Comma-separated list of severity levels to process. |
| `rsa_filter_priority_level` | | `"MEDIUM,HIGH,CRITICAL"`| Comma-separated list of priority levels to process. |
| `rsa_filter_relevance_level`| | `"MEDIUM,HIGH"` | Comma-separated list of relevance levels to process. |
| `rsa_filter_relevance_confidence`| | `"MEDIUM,HIGH"` | Comma-separated list of confidence levels to process. |
| `log_level` | | `"INFO"` | Standard Python logging level (e.g., `DEBUG`, `INFO`). |

#### Deployment Steps

1. **Configure Parameters:** Set your GCP Project ID, region, and GTI API key in the Terraform configuration variables.
2. **Run Terraform:** Execute `terraform init` and `terraform apply`.
3. **Automated Provisioning:** Terraform sets up the Entra ID bot registration, Azure Bot Service, GCP Cloud Run services, Firestore database, and Secret Manager secrets.
4. **Install in Teams:** Download the generated Teams app package (.zip) from Google Cloud Storage and upload it in Microsoft Teams: **Apps → Manage your apps → Upload a custom app**.

---

### Option 2: Azure Native Hosting (Target Architecture)

#### Configuration Parameters (`deploy.sh`)

| Environment Variable | Required | Default | Description |
| :--- | :---: | :--- | :--- |
| `GTI_API_KEY` | ✅ | | Your Google Threat Intelligence API key. |
| `ENABLE_RS_ALERTS` | | `false` | Set to `"true"` to enable the Real-time System (RS) Alert poller. |
| `RS_ALERTS_TEAMS_CHANNEL_ID` | | | The Teams channel URL/ID for RS Alerts (`19:...@thread.tacv2`). |
| `RS_ALERTS_GTI_PROJECT` | | | The GTI Project ID (required if RS alerts are enabled). |
| `RSA_SCHEDULE` | | `"0 */15 * * * *"`| The Azure NCRONTAB string for the RS Alerts timer trigger. |
| `RSA_BACKFILL_DAYS` | | `7` | Days of historical GTI alerts to fetch on first execution. |
| `RSA_PAGE_SIZE` | | `1000` | Number of alerts to retrieve per page from the GTI API. |
| `RSA_FILTER_SEVERITY_LEVEL` | | `"MEDIUM,HIGH"` | Comma-separated list of severity levels to process. |
| `RSA_FILTER_PRIORITY_LEVEL` | | `"MEDIUM,HIGH,CRITICAL"`| Comma-separated list of priority levels to process. |
| `RSA_FILTER_RELEVANCE_LEVEL`| | `"MEDIUM,HIGH"` | Comma-separated list of relevance levels to process. |
| `RSA_FILTER_RELEVANCE_CONFIDENCE`| | `"MEDIUM,HIGH"` | Comma-separated list of confidence levels to process. |

#### Simple Deployment via Azure Template

1. **Launch Template:** Open the Azure deployment template in the Azure Portal (or deploy via the `deploy.sh` script).
2. **Enter Parameters:** Provide your desired Function App name, select your Azure region, and enter your `GTI_API_KEY`. You can optionally supply `RSA_*` environment variables (e.g., `RSA_FILTER_SEVERITY_LEVEL`, `RSA_SCHEDULE_TIMEZONE`) to automatically configure the RS Alerts worker.
3. **Automated Provisioning:** Azure automatically provisions the Azure Function App (Flex Consumption), Key Vault, Managed Identity, Storage Account, Application Insights, and Azure Bot resource. RS Alerts tuning options are mapped directly to Function App settings.
4. **Deploy Code:** Publish the application package to the newly created Azure Function App.
5. **Install in Teams:** Download the automatically generated manifest zip package from the `teams-manifest` container in Azure Blob Storage and upload it in Teams (**Apps → Manage your apps → Upload a custom app**).

---

## User Perspective & Interaction Examples

### 1. Direct Messages & Group Chats (Interactive Lookups)

- **User Action:** The analyst types `@GTI analyze domain: evil-phishing-example.com`.
- **Immediate Response:** The bot replies with `⏳ Looking into that with Google Threat Intelligence…`.
- **Final Result:** Within seconds, the placeholder updates into a full threat report card containing:
  - Overall threat verdict (e.g., **Malicious / High Confidence**).
  - Detected malware families, associated threat actors, and categorization tags.
  - Sourced-from-GTI footer and a clickable button: **[View in GTI Console]**.

### 2. Team Channels (Automated Threat Notifications)

- Security operations teams monitor a dedicated `#threat-intel-feed` channel in Teams.
- When Google Threat Intelligence identifies high-priority threats matching the organization's filter criteria, an alert card automatically appears in the channel with full context and recommended remediation steps.

---

## References

- **Google Threat Intelligence (GTI) Agentic API** — `POST /agentspace/sessions/{session_id}` API reference.
- **Microsoft Teams Developer Platform** — Bot Framework protocol, Teams AI SDK, and Adaptive Card formatting specifications.
- **Microsoft Azure Cloud Architecture** — Azure Functions Flex Consumption, Azure Bot Service, Azure Key Vault, and Bicep deployment templates.
