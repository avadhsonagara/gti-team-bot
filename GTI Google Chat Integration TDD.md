# GTI Google Chat AI Integration

Technical Design Document

**Status:** Google Cloud Platform (GCP) Cloud Run is the native target architecture for this integration.

---

## Table of Contents

1. [Overview](#overview)
2. [Technical Requirements](#technical-requirements)
3. [Deployment Architecture & Strategy](#deployment-architecture--strategy)
4. [Security & Data Privacy](#security--data-privacy)
5. [System Architecture](#system-architecture)
6. [Deep Dive: The Bot Backend (FastAPI + BackgroundTasks)](#deep-dive-the-bot-backend-fastapi--backgroundtasks)
7. [Deep Dive: Security Pipeline (PII & Guardrails)](#deep-dive-security-pipeline-pii--guardrails)
8. [Deep Dive: GTI Agentic Sessions API](#deep-dive-gti-agentic-sessions-api)
9. [Deep Dive: Scalability, Resilience, & State Management](#deep-dive-scalability-resilience--state-management)
10. [Deployment Instructions](#deployment-instructions)
11. [User Perspective & Interaction Examples](#user-perspective--interaction-examples)
12. [References](#references)

---

## Overview

The **Google Threat Intelligence (GTI) Google Chat Integration** is a conversational threat investigation platform that enables security operations teams to access the full capabilities of the GTI Agentic Module directly within Google Workspace. 

Instead of switching between multiple security tools and manually correlating threat data, analysts can interact with the system using natural language queries such as IOC lookups, reputation checks, malware analysis, and intelligence searches directly in Google Chat spaces and Direct Messages.

### How It Works in Simple Terms

The Google Chat application acts as a secure orchestration layer between Google Workspace and the GTI Agentic Module:
1. **User asks a question:** An analyst asks a question in a Google Chat space (for example: `@GTIBot What malware families are associated with APT28?`).
2. **Bot fetches context:** The bot retrieves recent thread history to maintain conversational context.
3. **Bot forwards the query:** The bot securely forwards the user's question directly to Google's hosted threat intelligence agent.
4. **Google's agent investigates:** Google's hosted agent orchestrates lookups across GTI data sources and synthesizes the threat intelligence.
5. **Interactive response in Chat:** The bot receives the formatted result and renders it in Google Chat as a structured, easy-to-read markdown message with risk scores and actionable insights.

---

## Technical Requirements

### Functional Requirements

- **Conversational Context:** The system must maintain context within Google Chat threads by fetching recent messages, allowing the GTI agent to resolve pronouns and follow-up requests.
- **Google Chat Markdown Formatting:** Responses must strictly adhere to Google Chat's unique markdown dialect (e.g., single asterisks for bold `*bold*`, no markdown tables).
- **Structured Response Layouts:** Answers must follow a standardized format including Header, Summary & Verdict, Details, Risk & Signals, Recommended Actions, and Footer.

### Non-Functional Requirements

- **Performance & Timeouts:** Google Chat demands an HTTP 200 response within 30 seconds. Because agentic AI queries can take longer, the bot must acknowledge the webhook immediately and process the heavy lifting asynchronously.
- **Security & PII Redaction:** The bot must automatically detect and redact sensitive Personally Identifiable Information (PII) before sending prompts to external APIs, while carefully preserving actual threat indicators (IPs, Hashes, Domains).
- **Serverless Scalability:** The backend runs on a stateless Google Cloud Run container, scaling horizontally on demand.
- **Observability:** Unified structured JSON logging compatible with GCP Cloud Logging, including Correlation IDs and Cloud Trace header mapping.

---

## Deployment Architecture & Strategy

A production Google Chat bot consists of three main components:

```
Google Chat Users  ──►  Workspace Add-on Webhook  ──►  Bot Backend (GCP Cloud Run)  ──►  GTI Agentic API
```

1. **Workspace Add-on Profile:** Configuration within the Google Cloud Console defining the bot's name, avatar, and webhook endpoint.
2. **OIDC Verified Webhook:** Google's servers push HTTP `POST` events (like `MESSAGE` or `ADDED_TO_SPACE`) directly to the bot backend, secured by JWT signature verification.
3. **Bot Backend:** A serverless web application (FastAPI) that receives events, performs security scrubbing, calls GTI, and uses the Google Chat REST API to post the final answer back to the thread.

---

## Security & Data Privacy

We intentionally deploy this bot as Infrastructure-as-Code (Terraform) directly into your GCP environment to ensure the highest level of enterprise security:

- **Total Data Sovereignty:** By deploying into **your own GCP project**, all API keys, chat messages, and threat queries remain entirely inside your security boundary.
- **Inbound Request Verification:** Every incoming webhook is rigorously verified using `google-auth` to validate the OIDC JWT token. The audience claim must match the bot's exact HTTPS endpoint, guaranteeing the message actually came from Google Workspace.
- **Zero-Trust Identity:** The bot authenticates to the Google Chat API using Application Default Credentials (ADC) tied to a dedicated Cloud Run Service Account.
- **Secret Vaulting:** The only static secret required is the `GTI_API_KEY`. It is vaulted in **Google Secret Manager** and injected securely into the application memory at runtime.

---

## System Architecture

```
┌───────────────────────────────────────────────────────┐
│                 Google Workspace                      │
│                  (Google Chat)                        │
└───────────────────────────────────────────────────────┘
            │                               ▲
            │     Workspace Add-on          │
    (Events)▼     (OIDC Verified)           │ API POST
┌───────────────────────┐       ┌───────────────────────┐
│    GCP Cloud Run      │       │    GCP Gen2 Function  │
│    (Bot Backend)      │       │   (RSA Notifications) │
│                       │       │                       │
│ 1. Parse Event        │       │ 1. Cloud Scheduler    │
│ 2. Load Secrets (GSM) │       │ 2. Load Cursor (GCS)  │
│ 3. PII Filter & Checks│       │ 3. Fetch/Filter Alerts│
│ 4. Query GTI API      │       │ 4. Send GChat Message │
│ 5. Reply to Chat      │       │ 5. Save Cursor (GCS)  │
└───────────────────────┘       └───────────────────────┘
            │                               │
            ▼                               ▼
    GTI Agentic API                GTI Agentic API
  (sessions endpoint)             (alerts endpoint)
```

---

## Deep Dive: The Bot Backend (FastAPI + BackgroundTasks)

The interactive bot backend is built using **FastAPI** running on **Google Cloud Run**. 

### Bypassing the 30-Second Webhook Timeout
Google Chat enforces a strict 30-second timeout on all incoming webhooks. Because GTI Agentic queries often require multi-tool execution and LLM reasoning, they can occasionally exceed this limit.

To guarantee reliability, the FastAPI application immediately returns an `HTTP 200 OK` empty JSON payload upon receiving the webhook. It delegates the actual work (fetching thread history, calling GTI, and posting the response) to a **FastAPI `BackgroundTask`**. 

### Thread History & Conversational Context
The GTI Agentic API creates a new, stateless session per query. To maintain threaded conversations without managing a database, the bot uses the Google Chat API (`chat.messages.readonly` scope) to fetch the last 5 messages from the current thread. It prepends this history to the user's prompt, allowing the agent to effortlessly resolve pronouns and contextual follow-ups.

---

## Deep Dive: Security Pipeline (PII & Guardrails)

The application enforces a rigorous defense-in-depth pipeline on every single message *before* it leaves the GCP boundary.

### SOC-Aware Regex PII Filtering
To protect internal employee identities and systems, the bot scans user input and thread history with regex patterns. It automatically masks:
- Employee email addresses (`[REDACTED_EMAIL]`)
- Plaintext passwords and access tokens
- Sensitive internal hostnames

Critically, the logic is **SOC-aware**—it intentionally preserves actual cybersecurity indicators such as Hashes (MD5/SHA256), IPs, Domains, and CVEs so that the threat investigation remains accurate.

### Prompt-Based Guardrails (Injection Defense)
Strict guardrail rules are embedded natively into the system prompt to defend against jailbreaks without requiring external proxy infrastructure:
- **Identity Lock:** Instructs the LLM to act solely as a threat intelligence assistant.
- **Instruction-Override Defense:** Explicitly prohibits the agent from honoring "ignore previous instructions" or prompt injection payloads embedded within user queries.
- **Scope Restriction:** Rejects non-cybersecurity queries politely.

---

## Deep Dive: GTI Agentic Sessions API

The bot delegates all reasoning to Google's hosted **GTI Agentic Sessions API** via an `x-apikey` authentication header (`POST /api/v3/intelligence/sessions`).

### Google Chat Markdown Dialect Formatting
Unlike Teams (which uses Adaptive Cards) or Slack (which uses Block Kit), Google Chat relies entirely on a unique dialect of Markdown.
To ensure the LLM returns data that formats perfectly in Google Chat, the bot injects a hidden **System Prompt** instructing the agent to:
1. Use single asterisks for bold (`*bold*`), not double asterisks.
2. Avoid Markdown tables completely (Google Chat cannot render them). Convert tabular data into structured sub-bulleted lists instead.
3. Use clear section dividers (`---`).

---

## Deep Dive: Scalability, Resilience, & State Management

### RS Alerts Notifications (Standalone Function)
In addition to the interactive bot, the solution includes a **Real-time System (RSA) Alerting** workflow. This runs independently as a **Gen2 Cloud Function** triggered by a **Cloud Scheduler** job.

### Cursor Persistence & Batching
To guarantee that alerts are never sent twice and never lost during a crash, the RSA function maintains a strict `updateTime` cursor checkpoint stored in a **Google Cloud Storage (GCS)** bucket (`cursor.json`).
- If no cursor exists, it performs an initial backfill sweep.
- It queries GTI for new alerts matching the severity/priority filters.
- It groups alerts into batches respecting Google Chat's 4,000-character message limit, posting them cleanly with dividers and severity emoji markers.
- It atomically saves the latest processed timestamp back to GCS.

---

## Deployment Instructions

The infrastructure is managed via Terraform scripts located in the `terraform/` directory.

### Feature Flags (`terraform.tfvars`)

| Variable | Type | Default | Description |
| :--- | :--- | :--- | :--- |
| `configure_gchat_bot` | `bool` | `true` | Deploy the interactive Google Chat Bot stack (Cloud Run). |
| `configure_rsa_notifications` | `bool` | `false` | Deploy the RSA Notifications stack (Cloud Function + Scheduler). |

### Configuration Parameters

| Variable | Description |
| :--- | :--- |
| `project_id` | Your Google Cloud Project ID. |
| `region` | The Google Cloud region to deploy to (e.g., `us-central1`). |
| `gti_api_key` | Your Google Threat Intelligence API key. |
| `sa_key_file_path` | Local path to your Google Chat Service Account JSON key. |

### Step-by-Step Deployment Procedure

#### Step 1: Build & Push Container Image
```shell
export DOCKER_HUB_USER="yourusername"
export IMAGE="docker.io/$DOCKER_HUB_USER/gti-chat-bot:latest"
docker build -t $IMAGE .
docker push $IMAGE
```

#### Step 2: Create Google Chat Service Account
1. In the Google Cloud Console, navigate to **IAM & Admin > Service Accounts**.
2. Create a new service account (e.g., `gti-chat-bot-sa@<project-id>.iam.gserviceaccount.com`).
3. Download the JSON key file and save it as `secrets/chat-bot-sa-key.json`.

#### Step 3: Terraform Execution
```shell
cd terraform
terraform init
terraform apply \
  -var="project_id=your-gcp-project-id" \
  -var="region=us-central1" \
  -var="docker_image=docker.io/yourusername/gti-chat-bot:latest" \
  -var="gti_api_key=your-gti-api-key" \
  -var="sa_key_file_path=../secrets/chat-bot-sa-key.json"
```

#### Step 4: Configure OAuth Consent Screen (GCP Console)
Even though the bot uses a Service Account, Google requires the app to declare restricted scopes.
1. Go to **APIs & Services > OAuth consent screen**. Select **Internal** and click Create.
2. Under Scopes, add `https://www.googleapis.com/auth/chat.messages.readonly`.

#### Step 5: Workspace Admin Approval (Admin Console)
Because the bot requires read access to thread history, a Workspace Admin must approve the Client ID.
1. Copy the **Client ID** of your Service Account from the GCP Console.
2. In the Google Admin Console, go to **Security > Access and data control > API controls**.
3. Click **Manage Third-Party App Access > Add app > OAuth App Name Or Client ID**.
4. Paste the Client ID, select your users, and mark it as **Trusted**.

#### Step 6: Configure the Chat App Profile (GCP Console)
1. Go to **APIs & Services > Google Chat API > Configuration**.
2. Name the app and toggle **Enable interactive features**.
3. Under **Connection Settings**, select **App URL** and paste your Cloud Run URL appended with `/chat/events`.
4. Click **Save**.

---

## User Perspective & Interaction Examples

### 1. Single-IOC Evaluation
- **User Action:** The analyst types `@GTIBot analyze IP 1.1.1.1`.
- **Immediate Response:** The bot instantly processes the webhook and begins the investigation.
- **Final Result:** Within seconds, the bot posts a fully structured Google Chat markdown response containing the threat verdict (Malicious/Suspicious/Benign), key behavioral signals, and actionable insights.

### 2. Threaded Contextual Follow-ups
- **User Action:** Inside a response thread, the analyst asks `@GTIBot What file hashes are communicating with this domain?`
- **Follow-up:** Without restating the domain, the analyst asks `@GTIBot summarize the dynamic execution behaviors for the first hash.`
- **Result:** The bot fetches the last 5 messages, recognizes the pronouns ("this domain", "the first hash"), and instructs the GTI agent to provide a highly specific, contextual answer.

---

## References

- [Google Threat Intelligence (GTI) Documentation](https://gtidocs.virustotal.com/)
- [GTI Session API — Post a message to a new session](https://gtidocs.virustotal.com/reference/create-session)
- [Google Chat API Documentation](https://developers.google.com/workspace/chat)
- [Google Chat Markdown Syntax](https://developers.google.com/workspace/chat/format-messages)
- [FastAPI Framework Documentation](https://fastapi.tiangolo.com)
- [GCP Cloud Run Documentation](https://cloud.google.com/run/docs)