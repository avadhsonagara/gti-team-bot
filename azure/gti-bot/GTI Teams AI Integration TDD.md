# GTI Microsoft Teams AI Integration

Technical Design Document

**Status:** Approach B (Azure Native) is the primary target architecture. Approach A (GCP) is documented as the reference architecture for Google Cloud environments.

---

## Table of Contents

1. [Overview](#overview)
2. [Technical Requirements](#technical-requirements)
3. [Deployment Architecture & Strategy](#deployment-architecture--strategy)
4. [Why We Don't Publish the Bot to the Public Teams Marketplace](#why-we-dont-publish-the-bot-to-the-public-teams-marketplace)
5. [System Architecture — Two Approaches](#system-architecture--two-approaches)
   - [Approach A: GCP Hosting (Reference Architecture)](#approach-a-gcp-hosting-reference-architecture)
   - [Approach B: Azure Native Hosting (Target Architecture)](#approach-b-azure-native-hosting-target-architecture)
   - [Cloud Service Mapping Matrix](#cloud-service-mapping-matrix)
6. [Integration 1: GTI Teams Bot App](#integration-1-gti-teams-bot-app)
7. [GTI Agentic API Integration Detail](#gti-agentic-api-integration-detail)
8. [Integration 2: Real-time System (RS) Threat Alerts](#integration-2-real-time-system-rs-threat-alerts)
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

## Why We Don't Publish the Bot to the Public Teams Marketplace

We intentionally do **not** publish this bot as a public, multi-tenant app on the Microsoft Teams Marketplace for critical security reasons:

- **Customer Data Privacy:** A public marketplace app would require a single shared backend server that holds every customer's GTI API keys and processes all internal security queries in one shared environment.
- **Dedicated Cloud Deployment:** Instead, each customer deploys the bot into **their own cloud subscription** (Azure or GCP) using automated infrastructure templates.
- **Total Ownership:** All API keys, chat messages, and threat queries remain entirely inside the customer's own cloud security boundary.

---

## System Architecture — Two Approaches

### Approach A: GCP Hosting (Reference Architecture)

For organizations hosting their workloads in Google Cloud:

```
Microsoft Teams
      │
      ▼
 Azure Bot Service
      │
      ▼
 GCP Cloud Run (Bot Backend)  ◄──►  GCP Secret Manager (GTI API Key)
      │                        ◄──►  Firestore (Checkpoints & Settings)
      ▼
 GTI Agentic API (Google Threat Intelligence /agentspace/sessions/{session_id})

 GCP Cloud Run Function (Alert Poller)  ◄──►  Firestore (Checkpoint Cursor)
      │
      ▼
 Microsoft Teams Channel Webhook
```

- **Compute:** GCP Cloud Run runs the interactive bot backend as an auto-scaling container service.
- **Secrets Management:** The GTI API key is stored securely in **GCP Secret Manager** and provided to the application securely at startup.
- **State Storage:** **Firestore** stores configuration settings and alert tracking cursors.
- **Infrastructure as Code:** Automated with Terraform templates.

---

### Approach B: Azure Native Hosting (Target Architecture)

For organizations whose primary cloud ecosystem is Microsoft Azure:

```
Microsoft Teams
      │
      ▼
 Azure Bot Service  ◄── User-Assigned Managed Identity (Secure authentication without secrets)
      │
      ▼
 Azure Functions (Python)  ◄──►  Azure Key Vault (Secure GTI API Key storage)
      │                     ◄──►  Application Insights (Logs & Health Monitoring)
      ▼
 GTI Agentic API (Google Threat Intelligence /agentspace/sessions/{session_id})

 Azure Blob Storage  ← Teams App Manifest Archive & Alert Checkpoints
```

- **Compute:** **Azure Functions (Flex Consumption, Python)** hosts the bot application. It scales on demand and executes requests rapidly with zero idle infrastructure cost.
- **Passwordless Authentication:** The bot uses a **User-Assigned Managed Identity**. This allows Azure Bot Service and Azure Functions to securely authenticate with each other without creating, managing, or rotating client passwords.
- **Secret Protection:** The `GTI_API_KEY` is stored in **Azure Key Vault**. Azure Functions reads the key securely at runtime using its Managed Identity with the Key Vault Secrets User role.
- **Blob Storage:** An **Azure Storage Account** stores the deployment package, the generated Teams app manifest archive, and alert tracking checkpoints.
- **Monitoring & Observability:** **Application Insights** automatically collects performance metrics, error rates, and end-to-end request tracking IDs.
- **Automated Infrastructure:** Full setup is automated using Azure Bicep templates, supporting single-command deployments and one-click deployment buttons.

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

## Integration 1: GTI Teams Bot App

### Overview

The bot backend is built using a modern web framework (FastAPI) running as a serverless ASGI application. It provides standard HTTP endpoints:

| Endpoint Route | HTTP Method | Purpose |
| :--- | :--- | :--- |
| `/` | `GET` | Service status and welcome information |
| `/health` | `GET` | Health check probe |
| `/api/messages` | `POST` | Core messaging endpoint for Microsoft Teams activities |

### Key Design Highlights

- **Direct API Model:** The bot does not run its own complex local language model. Instead, it delegates reasoning and threat intelligence data aggregation directly to Google's hosted agent via a single secure API call per query.
- **Asynchronous Architecture:** All network operations and API calls are fully asynchronous, ensuring fast response times.
- **Zero-Secret Identity on Azure:** Uses Azure Managed Identities to eliminate password expiration and secret leak risks.

### Core Application Components

1. **Application Entry & Routing Layer:** Manages incoming web requests and connects the Microsoft Teams SDK with the application endpoints.
2. **Centralized Configuration:** Loads environment variables, API keys, connection strings, and timeout settings safely at application startup.
3. **GTI Agent Client:** Manages HTTP communication with Google Threat Intelligence, handling automatic retries, backoff delays, and network resilience.
4. **Teams Message Pipeline:**
   - Cleans the incoming message text by removing mention tags (`@GTI`).
   - Validates that the query contains meaningful text; if empty, replies with usage tips.
   - Posts a temporary loading card in Teams (`⏳ Looking into that with Google Threat Intelligence…`).
   - Dispatches the prompt to Google Threat Intelligence's Agentic API.
   - Parses the returned threat analysis and converts it into a visual Teams Adaptive Card.
   - Updates the temporary message with the final Adaptive Card.
5. **Adaptive Card Builder:** Assembles structured visual cards that display threat severity tags, key evidence facts, timestamps, and direct web links to the Google Threat Intelligence console.
6. **Error & Fallback Handling:** If Google Threat Intelligence is temporarily unavailable or returns an error, the bot delivers an informative, user-friendly card describing the situation.

---

## GTI Agentic API Integration Detail

The bot communicates with the **GTI Agentic Sessions API** using an `x-apikey` authentication header.

### Primary API Endpoint

`POST /agentspace/sessions/{session_id}`

This endpoint sends a message to an active threat investigation session and synchronously returns the updated session state along with the agent's findings.

- **Path Parameter:** `session_id` (string, required) — The unique identifier for the conversation session.
- **Request Body (`multipart/form-data`):**
  - `message` (string, required) — The analyst's query along with system formatting instructions.
  - `files` (array of files, optional) — Optional file attachments or artifact indicators.

### Key Interaction Characteristics

- **Response Extraction:** The bot reads the event stream returned by Google Threat Intelligence and extracts the final threat summary and markdown findings.
- **Built-in Resilience:** The client automatically retries transient errors (`429` rate limits, `5xx` server issues, connection timeouts) with exponential backoff before reporting an issue.
- **Output Formatting:** A standardized system prompt instructs the hosted GTI agent to return structured data suitable for direct presentation in Microsoft Teams Adaptive Cards.

---

## Integration 2: Real-time System (RS) Threat Alerts

### Overview

In addition to interactive questions from analysts, the solution supports an automated **Real-time System (RS) Alerting** workflow. This background task continuously checks Google Threat Intelligence for newly detected security events and posts alert cards into a designated Teams channel.

- **Fully Automated:** Runs independently on a schedule without needing user interaction.
- **No Duplicate Alerts:** Remembers the timestamp of the last delivered alert (using a persistent cursor checkpoint) so alerts are never sent twice.
- **Configurable Filters:** Allows security teams to filter incoming alerts based on minimum severity, priority level, and confidence scores (e.g., High and Critical only).

### Execution Flow

1. **Trigger:** A scheduled timer runs at regular intervals (e.g., every 5 minutes).
2. **Fetch Checkpoint:** The service reads the last saved alert timestamp from cloud storage.
3. **Query GTI Alerts API:** The service calls the Google Threat Intelligence alerts API to request new alerts updated since that timestamp.
4. **Filter & Format:** Matching alerts are converted into structured Adaptive Cards highlighting threat type, affected assets, and recommended actions.
5. **Publish & Update:** Cards are sent to the designated Teams channel webhook. After successful delivery, the new checkpoint timestamp is saved.

---

## Deployment Instructions

### Option 1: GCP Hosting (Reference)

1. **Configure Parameters:** Set your GCP Project ID, region, and GTI API key in the Terraform configuration variables.
2. **Run Terraform:** Execute `terraform init` and `terraform apply`.
3. **Automated Provisioning:** Terraform sets up the Entra ID bot registration, Azure Bot Service, GCP Cloud Run services, Firestore database, and Secret Manager secrets.
4. **Install in Teams:** Download the generated Teams app package (.zip) from Google Cloud Storage and upload it in Microsoft Teams: **Apps → Manage your apps → Upload a custom app**.

---

### Option 2: Azure Native Hosting (Target Architecture)

#### Simple Deployment via Azure Template

1. **Launch Template:** Open the Azure deployment template in the Azure Portal.
2. **Enter Parameters:** Provide your desired Function App name, select your Azure region, and enter your `GTI_API_KEY`.
3. **Automated Provisioning:** Azure automatically provisions the Azure Function App (Flex Consumption), Key Vault, Managed Identity, Storage Account, Application Insights, and Azure Bot resource.
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
