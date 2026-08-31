![][image1]

# **GTI Microsoft Teams Integration**

Technical Design Document

**Status:** Approach B (Azure Native) is the primary target architecture. Approach A (GCP) is documented as the reference architecture for Google Cloud environments.

**Table of Contents**

---

- [Overview](#overview)
- [Problem Statement](#problem-statement)
- [Solution](#solution)
- [Target Use Cases & Capabilities](#target-use-cases--capabilities)
  - [2.1 Supported Functional Modules](#21-supported-functional-modules)
  - [2.2 Example Interaction Patterns](#22-example-interaction-patterns)
- [Technical Requirements](#technical-requirements)
  - [Functional Requirements](#functional-requirements)
    - [Conversation Support](#conversation-support)
    - [Data Handling & Response Formatting](#data-handling--response-formatting)
    - [Security Controls: PII & Guardrails](#security-controls-pii--guardrails)
  - [Non-Functional Requirements](#non-functional-requirements)
- [Integration 1: GTI Teams Bot App](#integration-1-gti-teams-bot-app)
  - [Architecture Diagram](#architecture-diagram)
  - [Activity Diagram](#activity-diagram)
  - [Technology Stack](#technology-stack)
  - [Core Application Components](#core-application-components)
    - [Application Entry Point & Web Server](#application-entry-point--web-server)
    - [Centralized Configuration](#centralized-configuration)
    - [GTI Agentic Pipeline](#gti-agentic-pipeline)
    - [System Prompt Template](#system-prompt-template)
    - [Teams Activity & Event Handlers](#teams-activity--event-handlers)
    - [Shared Utilities](#shared-utilities)
- [Security Pipeline](#security-pipeline)
  - [Why We Don't Publish the Bot to the Public Teams Marketplace](#why-we-dont-publish-the-bot-to-the-public-teams-marketplace)
  - [Prompt-Based Guardrails](#prompt-based-guardrails)
  - [Built-in Regex PII Filter](#built-in-regex-pii-filter)
  - [Azure Bot Service & Tenant Verification](#azure-bot-service--tenant-verification)
- [Observability and Logging](#observability-and-logging)
  - [Structured Logging](#structured-logging)
  - [Per-Request Context](#per-request-context)
- [Integration 2: RSA Notifications](#integration-2-rsa-notifications)
  - [RSA Architecture Diagram](#rsa-architecture-diagram)
  - [RSA Implementation Detail](#rsa-implementation-detail)
  - [Alert Filtering and Batching](#alert-filtering-and-batching)
    - [Filter Dimensions](#filter-dimensions)
    - [Batching](#batching)
    - [Alert Formatting](#alert-formatting)
  - [State Management](#state-management)
- [Deployment (Azure Bicep / Terraform)](#deployment-azure-bicep--terraform)
  - [Feature Flags & Configuration Parameters](#feature-flags--configuration-parameters)
  - [Infrastructure Resources](#infrastructure-resources)
  - [Deployment Procedure](#deployment-procedure)
    - [Approach B: Azure Native Deployment (Target)](#approach-b-azure-native-deployment-target)
    - [Approach A: GCP Cloud Run Deployment (Reference)](#approach-a-gcp-cloud-run-deployment-reference)
    - [Step: Teams App Manifest Installation](#step-teams-app-manifest-installation)
- [Limitations](#limitations)
- [References](#references)

# 

## **Overview**

---

The GTI Microsoft Teams AI integration is a conversational threat investigation platform that enables security analysts to access the full capabilities of the Google Threat Intelligence (GTI) Agentic Module directly within Microsoft Teams.

Instead of switching between multiple security tools and manually correlating threat data, analysts can interact with the system using natural language queries such as IOC lookups, reputation checks, malware analysis, threat actor investigations, and intelligence searches directly within Teams 1:1 direct chats, group conversations, and shared team channels.

The Microsoft Teams application acts as an orchestration layer between Microsoft Teams (via Azure Bot Service) and the GTI Agentic Module — Google's hosted AI system that interprets analyst intent, invokes the appropriate GTI tools, processes complex threat intelligence data, and returns concise, structured, and actionable insights within Teams as interactive Adaptive Cards.

This architecture delegates all AI reasoning and tool execution to GTI's native "Post a message to a new session" REST API endpoint. This eliminates the need to manage external LLM infrastructure, MCP servers, or complex agent orchestration frameworks — the GTI backend handles all of that transparently.

## 

## **Problem Statement**

---

Threat analysts deal with a constant stream of alerts. While these contain critical indicators (hashes, IPs, domains), understanding their true impact is difficult due to:

* **Context Switching**: Analysts manually jump between multiple tools and platforms.  
* **Data Overload**: Raw threat data is often large, complex, and difficult to interpret quickly.  
* **Information Silos**: Context such as relationships and threat attributes are scattered across systems.  
* **Operational Friction**: Repetitive lookup tasks consume significant time that should be spent on active investigation.

## 

## **Solution**

---

We propose a Microsoft Teams-integrated threat investigation application powered by the GTI Agentic Module.

**Unified Interface**: Analysts initiate investigations directly in Teams using natural mentions: `@GTI investigate IP 8.8.8.8` or `@GTI what malware families are associated with APT28?`.  
**GTI-Native AI Reasoning**: The system passes user queries directly to GTI's hosted agentic endpoint. GTI autonomously retrieves real-time threat intelligence, executes the appropriate tools, and synthesizes raw data into structured, actionable intelligence summaries formatted as Adaptive Cards — no custom LLM or MCP setup required.  
**Interactive Thread Context**: Analysts can conduct deep, multi-turn investigations through follow-up queries inside Teams threads. The bot automatically retrieves and prepends recent conversation context to each query, ensuring GTI can resolve references and follow-ups without the analyst restating indicators.

## 

## **Target Use Cases & Capabilities**

---

The application exposes the comprehensive feature set of the GTI Agentic Module via the GTI Session API.

### **2.1 Supported Functional Modules**

**Collections & Threat Profiles**

* Query active actor campaigns, targeted industries, and strategic intelligence reports.  
* Available Actions:  
  * Retrieve specific collection reports  
  * General threat search  
  * Campaign-specific searches  
  * Threat actor searches  
  * Malware family searches  
  * Toolkit searches  
  * Report searches  
  * Vulnerability searches  
  * Curated timeline events  
  * Related entity mapping  
  * List configured threat profiles  
  * Retrieve specific profile  
  * Profile-based recommendations  
  * Profile association history

**File & Malware Analysis**

* Retrieve dynamic analysis, behavior reports, drop structures, and static parameters for file hashes (SHA256/MD5/SHA1)  
* Available Actions:  
  * Comprehensive file analysis reports  
  * Specific sandbox behavior reports  
  * Aggregated behavior summaries  
  * Related domains, IPs, URLs, behaviors

**Intelligence Search**

* Execute advanced queries across standard threat parameters  
* Available Actions:  
  * Advanced IOC search (files, URLs, domains, IPs)

**Network Locations & URLs**

* Perform deep evaluations on IP addresses, domains, and specific URLs  
* Available Actions:  
  * Comprehensive domain analysis  
  * IPv4/IPv6 analysis  
  * URL-specific verdicts and analysis  
  * Domain relationship mapping  
  * IP relationship mapping  
  * URL relationship mapping

**Hunting**

* Leverage advanced cross-correlation features to identify active infrastructure patterns  
* Available Actions:  
  * Retrieve hunting ruleset objects  
  * Ruleset entity relationships

### **2.2 Example Interaction Patterns**

* **Single-IOC Evaluation:** `@GTI analyze IP 1.1.1.1`  
* **Domain Check:** `@GTI is bad-domain.com associated with known campaigns?`  
* **Threaded Follow-ups:** Inside a response thread: *"@GTI What file hashes are communicating with this domain?"* followed by *"@GTI summarize the dynamic execution behaviors for the first hash."*

## 

## **Technical Requirements**

---

### **Functional Requirements**

#### **Conversation Support**

* **New Conversations**: Ability to initiate a fresh threat investigation from any authorized Microsoft Teams channel, group chat, or direct message (1:1) using the `@GTI` mention.  
* **Immediate Progress Feedback:** When an analyst submits a query, the bot immediately posts a temporary message (`⏳ Looking into that with Google Threat Intelligence…`). Once the investigation finishes, this message is automatically updated in-place with the final Adaptive Card.

#### **Data Handling & Response Formatting**

* **Intelligent Summarization**: GTI's agentic module handles raw tool output synthesis. The bot is responsible for relaying the final GTI response in an interactive Adaptive Card format.  
* **Signal Highlighting**: Automatically extract and emphasize the Verdict (Malicious, Suspicious, Benign), Risk Score, and Severity from the GTI response.  
* **Teams Adaptive Card Formatting**: All responses follow a standardized Adaptive Card schema:  
  * **Header**: Visual title, query type badge, and indicator scope.  
  * **Summary & Verdict**: High-level verdict, risk score gauge, and primary threat attribution.  
  * **Details**: Technical attributes, network communications, and file indicators.  
  * **Risk & Signals**: Detection engine counts, sandbox behavior flags, and MITRE ATT&CK TTPs.  
  * **Recommended Actions**: Actionable next steps for SOC incident handlers.  
  * **Footer & Action Buttons**: Provenance indicator, UTC timestamp, and clickable **[View in GTI Console]** action button.

#### **Security Controls: PII & Guardrails**

To maintain enterprise privacy and system integrity, the Teams App implements security controls before and during prompt execution with the GTI Session API:

1. **PII (Personally Identifiable Information) Filtering**:  
* **Inbound Redaction**: Scans and masks internal employee names, internal system hostnames, and credentials before data is sent to GTI.  
* **Mechanism**: Uses Regex-based pattern matching to replace sensitive strings with generic placeholders (e.g., `[REDACTED_EMAIL]`). SOC-aware logic preserves threat intelligence indicators (hashes, IPs, domains) while redacting personally identifiable data.  
2. **Prompt-Based AI Guardrails & Injection Protection**:  
* **System Prompt Defense**: Embeds strict guardrail rules into the system prompt passed to GTI's agentic module to resist prompt injection, instruction override attempts ("ignore previous instructions"), role-play requests ("act as", "pretend to be"), and system prompt leak requests.  
* **Scope Restriction**: Directs the agent to only respond to cybersecurity and threat intelligence topics, politely rejecting generic or out-of-scope requests.

### **Non-Functional Requirements**

* **Security**: Secure handling of all credentials via Azure Key Vault (or Google Secret Manager); Managed Identity / OIDC token verification on all inbound activities from Azure Bot Service.  
* **Performance**: Synchronous processing with immediate progressive feedback card, backed by persistent HTTP connection pooling to minimize latency.  
* **Scalability**: Stateless serverless compute deployment on Azure Functions (Flex Consumption) or Google Cloud Run with automatic horizontal autoscaling.  
* **Reliability**: Graceful handling of GTI API timeouts or errors with exponential backoff retries, with user-friendly error cards posted back to Teams.  
* **Observability**: Unified structured logging compatible with Azure Application Insights / GCP Cloud Logging, with correlation IDs and distributed transaction tracing.

## **Integration 1: GTI Teams Bot App** 

---

The GTI Teams Bot App is a **FastAPI + ASGI** HTTPS application deployed on **Azure Functions (Flex Consumption)** or **Google Cloud Run**. It connects to Microsoft Teams via the **Azure Bot Service** communication bridge.

**Native GTI Agentic Architecture**: The integration directly leverages GTI's hosted Agentic Module. There is no external LLM to manage and no MCP Server to deploy. The entire AI reasoning, tool discovery, and data synthesis pipeline is handled by the **GTI Agentic Module** via a single REST API call to the GTI Session endpoint.

**Key design decisions:**

* **FastAPI + Serverless ASGI**: A single FastAPI service receives Bot Framework activities at `/api/messages`. Wrapped via `azure.functions.AsgiFunctionApp` on Azure or `a2wsgi` on GCP for seamless cloud portability.  
* **Zero-Secret Identity on Azure**: Leverages User-Assigned Managed Identities for passwordless access between Azure Bot Service, Azure Functions, and Key Vault.  
* **Immediate Progressive Feedback**: Posts a temporary "investigating" card immediately, executes the GTI reasoning query, and updates the existing message activity in-place with the final Adaptive Card once completed.  
* **GTI Session API as the AI Brain**: Instead of running a local Gemini LLM agent, the app constructs a structured prompt (including system formatting rules and security guardrails) and posts it to GTI's `POST /agentspace/sessions/{session_id}` endpoint.

### **Architecture Diagram**

**Approach B: Azure Native Hosting (Target Architecture)**

```
┌──────────────────────────────────────────────┐
│           Microsoft Teams Client             │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│              Azure Bot Service               │
│   (Bot Framework Proxy / Authentication)     │
└──────────────────────┬───────────────────────┘
                       │ HTTPS POST /api/messages
                       ▼ (Managed Identity)
┌──────────────────────────────────────────────┐
│       Azure Functions (Flex Backend)         │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │          FastAPI ASGI Router           │  │
│  │      • /api/messages  • /health        │  │
│  └───────────────────┬────────────────────┘  │
│                      │                       │
│                      ▼                       │
│  ┌────────────────────────────────────────┐  │
│  │        Azure Key Vault Client          │  │
│  │      • Secure GTI_API_KEY Ingestion    │  │
│  └───────────────────┬────────────────────┘  │
│                      │                       │
│                      ▼                       │
│  ┌────────────────────────────────────────┐  │
│  │        GTI Agent Client Module         │  │
│  │      • Prompt Assembly & Guardrails    │  │
│  │      • Connection Pool & Retries       │  │
│  └───────────────────┬────────────────────┘  │
│                      │                       │
│                      ▼                       │
│  ┌────────────────────────────────────────┐  │
│  │         Adaptive Card Builder          │  │
│  │      • Threat Verdict & Risk Gauge     │  │
│  └────────────────────────────────────────┘  │
└──────────────────────┬───────────────────────┘
                       │ HTTPS POST x-apikey
                       ▼
┌──────────────────────────────────────────────┐
│        Google Threat Intelligence (GTI)      │
│               Agentic Sessions API           │
│      POST /agentspace/sessions/{session_id}  │
└──────────────────────────────────────────────┘
```

**Approach A: GCP Cloud Run Hosting (Reference Architecture)**

```
┌──────────────────────────────────────────────┐
│           Microsoft Teams Client             │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│              Azure Bot Service               │
│   (Bot Framework Proxy / Authentication)     │
└──────────────────────┬───────────────────────┘
                       │ HTTPS POST /api/messages
                       ▼ (OIDC / App Secret)
┌──────────────────────────────────────────────┐
│         GCP Cloud Run Service (FastAPI)      │
│                                              │
│  ┌────────────────────────────────────────┐  │
│  │           FastAPI via a2wsgi           │  │
│  │      • /api/messages  • /health        │  │
│  └───────────────────┬────────────────────┘  │
│                      │                       │
│                      ▼                       │
│  ┌────────────────────────────────────────┐  │
│  │          GCP Secret Manager            │  │
│  │      • Loads GTI_API_KEY Secret        │  │
│  └───────────────────┬────────────────────┘  │
│                      │                       │
│                      ▼                       │
│  ┌────────────────────────────────────────┐  │
│  │   GTI Agent Pipeline & Card Builder    │  │
│  └────────────────────────────────────────┘  │
└──────────────────────┬───────────────────────┘
                       │ HTTPS POST x-apikey
                       ▼
┌──────────────────────────────────────────────┐
│        Google Threat Intelligence (GTI)      │
│               Agentic Sessions API           │
└──────────────────────────────────────────────┘
```

### **Activity Diagram**

```
Analyst            Azure Bot Svc       Bot Backend        GTI Sessions API
   │                     │                  │                     │
   │── 1. @GTI query ───►│                  │                     │
   │   "Analyze 8.8.8.8" │── 2. Activity ──►│                     │
   │                     │                  │── 3. Validate Token │
   │                     │                  │      & PII Filter   │
   │                     │◄── 4. Placehldr ─│                     │
   │◄── 5. Show "⏳..." ─│      Card        │                     │
   │                     │                  │── 6. Query Prompt ─►│
   │                     │                  │      (API Key)      │
   │                     │                  │                     │ (GTI Agent
   │                     │                  │                     │  executes
   │                     │                  │                     │  tools)
   │                     │                  │◄── 7. Verdict ──────│
   │                     │                  │                     │
   │                     │                  │── 8. Build Card     │
   │                     │◄── 9. Update Msg │                     │
   │◄── 10. Final Card ──│      (In-place)  │                     │
   │    (Verdict/Links)  │                  │                     │
```

### **Technology Stack**

| Component | Technology | Details |
| :---- | :---- | :---- |
| **Web Framework** | FastAPI + ASGI | High-performance async Python web framework; `/api/messages` endpoint |
| **Deployment (Target)** | Azure Functions (Flex) | Serverless Python compute with fast virtual network scaling |
| **Deployment (Ref)** | Google Cloud Run | Stateless containerized auto-scaling HTTPS service |
| **GTI Intelligence** | GTI Agentic Sessions API | `POST /agentspace/sessions/{session_id}`; hosted AI agent handles reasoning |
| **Chat Platform** | Microsoft Teams | Enterprise collaboration platform; interactive channel & 1:1 bots |
| **Bot SDK / Proxy** | Azure Bot Service | Microsoft Bot Framework protocol proxy and channel gateway |
| **Request Verification** | Microsoft Entra ID / OIDC | Managed Identity & JWT token validation on inbound bot activities |
| **PII Protection** | Regex PII Filter | SOC-aware regex patterns preserving threat intel indicators while redacting PII |
| **Guardrails** | Prompt-Based Guardrails | Strict system rules embedded in the prompt enforcing identity, scope, and injection defense |
| **Secret Management** | Azure Key Vault / GSM | Secure storage and runtime injection of the `GTI_API_KEY` |
| **Infrastructure / IaC** | Azure Bicep / Terraform | Infrastructure-as-Code automating Functions, Key Vault, Bot Service, and IAM |
| **Observability** | Application Insights / Trace | End-to-end distributed tracing, dependency metrics, and structured logs |

### **Core Application Components**

#### **Application Entry Point & Web Server**

The application entry point establishes the FastAPI HTTP service with the following endpoints and lifecycle management:

* **POST /api/messages**: Receives all Microsoft Teams Bot Framework activity payloads (message events, mentions, conversation updates). Performs authentication and dispatches to the Teams message pipeline.  
* **GET /health**: Returns status details for service health checks and deployment readiness probes.  
* **GET /**: Returns service welcome status and operational metadata.  
* **Lifespan Management**: Initializes structured logging, validates required environment variables, initializes a shared `httpx.AsyncClient` connection pool, and logs service diagnostics upon startup.

#### **Centralized Configuration**

All runtime configuration is loaded safely at startup:

* **GTI**: GTI API Key (`GTI_API_KEY`); GTI Session endpoint URL; max retries and timeout settings.  
* **Bot Framework**: Microsoft App ID, App Password (if non-managed identity), Tenant ID, and Bot endpoint.  
* **Security**: Authorized tenant allowlist; PII redaction patterns.  
* **Cloud Runtime**: Azure Key Vault URI or GCP Project ID and Region.

#### **GTI Agentic Pipeline**

The GTI agentic pipeline is the core intelligence component. It is a lightweight HTTP client that delegates all AI reasoning and tool execution to GTI:

1. **Prompt Construction**: Assembles the final prompt from:  
* **System Rules**: Strict instructions embedded in the prompt defining the bot's identity, scope (GTI-only), formatting expectations (Markdown structure for Adaptive Cards), and security posture.  
* **User Query**: The sanitized user query text (with `@GTI` mention stripped).  
2. **GTI Session API Call**: Issues a POST request to `POST /agentspace/sessions/{session_id}` with:  
   - Header: `x-apikey: <GTI_API_KEY>`  
   - Body: Multipart form data with `message: <assembled_prompt>`  
3. **Response Extraction**: Reads the stream returned by Google Threat Intelligence and extracts the final threat summary and markdown findings.  
4. **Error Handling**: Uses `tenacity` for exponential backoff retries on `429 Too Many Requests` or `5xx` errors.

#### **System Prompt Template**

The prompt template is assembled at runtime and passed as the query to the GTI Session API. Key elements:

**Security Policies**:
* Identity lock: The bot identifies itself as a GTI threat intelligence assistant only.  
* Instruction-override resistance: Explicit guard against "ignore all previous instructions" patterns.  
* Scope restriction: Reject non-cybersecurity queries politely.

**Data Minimization Rules**:
* Request only the minimum necessary information.  
* When the user asks for "verdict only", return just the concise verdict.

**Adaptive Card Markdown Formatting Constraints**:
* Format output using clear markdown headers (`###`), bullet points, and key-value sections.  
* Emphasize the definitive threat verdict (Malicious, Suspicious, Safe).

#### **Teams Activity & Event Handlers**

Handles primary activity types pushed by Microsoft Teams:

* **Message Activities (Channels & DMs)**: Triggered when a user mentions `@GTI` in a channel or sends a 1:1 direct message. Strips bot mention tags, runs the security pipeline, posts the progressive placeholder card, calls the GTI Session API, builds the final Adaptive Card, and updates the placeholder message activity.  
* **ConversationUpdate (Bot Added)**: Returns an informative welcome card explaining bot capabilities and example queries.

The query handling sequence:
1. **Strip @mention**: Remove the bot's mention tag from `activity.text` to extract the clean query.  
2. **Security Check**: Run guardrail check (injection detection) and PII filter on the user query.  
3. **Progressive Feedback**: Send an immediate `⏳ Looking into that with Google Threat Intelligence…` card and capture the `activity_id`.  
4. **Prompt Assembly**: Build the final prompt combining system rules and the sanitized query.  
5. **GTI Session API Call**: POST the prompt to GTI and receive the agent's response.  
6. **Adaptive Card Generation**: Parse the response and generate the rich Adaptive Card JSON payload.  
7. **Message In-Place Update**: Update the earlier placeholder message activity using the Bot Framework connector.

#### **Shared Utilities**

* **Card Builder**: Serializes threat analysis, risk scores, MITRE ATT&CK indicators, and GTI web links into valid Microsoft Teams Adaptive Card JSON.  
* **Notice Strings**: Standardized error and informational constants:  
  * `EMPTY_QUERY_NOTICE`: Posted when the user sends a mention with no query text.  
  * `GUARDRAIL_BLOCKED_NOTICE`: Posted when a prompt injection is detected.  
  * `GTI_ERROR_NOTICE`: Posted when the GTI Session API returns an unrecoverable error.  
* **Message Deliverer**: Wraps Bot Framework connector client to send new messages and update existing message activities.

## 

## **Security Pipeline**

---

The security pipeline enforces defense-in-depth on every inbound user query using request verification, regex-based PII redaction, and prompt-based guardrails.

### **Why We Don't Publish the Bot to the Public Teams Marketplace**

We intentionally do **not** publish this bot as a public, multi-tenant app on the Microsoft Teams Marketplace for critical security reasons:

- **Customer Data Privacy:** A public marketplace app would require a single shared backend server that holds every customer's GTI API keys and processes all internal security queries in one shared environment.  
- **Dedicated Cloud Deployment:** Instead, each customer deploys the bot into **their own cloud subscription** (Azure or GCP) using automated infrastructure templates.  
- **Total Ownership:** All API keys, chat messages, and threat queries remain entirely inside the customer's own cloud security boundary.

### **Prompt-Based Guardrails**

Guardrails are implemented natively by embedding strict, deterministic system rules into every prompt submitted to the GTI Session API:

* **Identity & Role Lock**: The model is explicitly instructed to act solely as the Google Threat Intelligence Assistant, rejecting attempts to reassign its persona or role.  
* **Instruction-Override Defense**: Explicit system instructions prohibit honoring "ignore previous instructions", "forget rules", or prompt injection payloads embedded within user queries.  
* **Strict Scope Restriction**: The agent is restricted strictly to cybersecurity, threat intelligence, and IOC investigation domains. Out-of-scope requests are politely declined.  
* **Data Minimization Constraints**: Enforces concise outputs adhering to structured formats to prevent prompt leakage and payload distortion.

### **Built-in Regex PII Filter**

**SOC-Aware PII Masking**:
* Inspects user input and masks personally identifiable information (PII) before transmission to external endpoints.  
* Preserves cybersecurity indicators: Hashes (MD5, SHA1, SHA256), IPv4/IPv6 addresses, domains, URL paths, CVE IDs, and threat actor names remain unmasked.  
* Redacts: Employee email addresses, plaintext passwords, credentials, access tokens, and sensitive internal hostnames using standardized placeholders (e.g., `[REDACTED_EMAIL]`).

### **Azure Bot Service & Tenant Verification**

* Validates the incoming JWT bearer token issued by Microsoft Bot Framework / Entra ID on every request.  
* Validates that the activity originated from an authorized Microsoft Tenant ID.  
* In Azure native deployments, Managed Identity completely eliminates the storage of client secrets.

## 

## **Observability and Logging**

---

### **Structured Logging**

* **Azure Production Mode** — Automatically streams logs, application metrics, and execution traces into **Azure Application Insights**.  
* **GCP Production Mode** — Generates JSON logs compatible with **GCP Cloud Logging**.

### **Per-Request Context**

* Tracks requests using a unique Correlation ID generated per activity.  
* Correlates inbound Bot Framework activity IDs with outgoing GTI Session requests for end-to-end auditability.  
* Captures operational metadata (channel name, tenant ID, user display name) without logging raw analyst queries to protect confidentiality.

## 

## **Integration 2: RSA Notifications** 

---

RSA Notifications is an automated integration that fetches GTI threat alerts on a schedule and pushes formatted alert cards to a designated Microsoft Teams channel.

* **Independent Execution**: Runs as a separate timer-triggered Azure Function (or GCP Gen2 Cloud Function triggered by Cloud Scheduler).  
* **Incremental Cursor-Based Ingestion**: Persists a timestamp checkpoint in Azure Blob Storage (or GCP Firestore) to ensure each alert is processed and delivered exactly once.  
* **Configurable Multi-Dimensional Filtering**: Filters incoming alerts by Severity, Priority, Relevance, and Confidence levels via environment variables.

### **RSA Architecture Diagram**

```
┌──────────────────────────────────────────────┐
│           Scheduled Timer Trigger            │
│            (e.g., Every 15 mins)             │
└──────────────────────┬───────────────────────┘
                       │
                       ▼
┌──────────────────────────────────────────────┐
│             Alert Poller Service             │
│      (Azure Function / GCP Cloud Run)        │
└───────────┬──────────────────────┬───────────┘
            │                      │
            ▼ (Read/Write)         ▼ (Query new)
┌───────────────────────┐  ┌───────────────────┐
│     State Storage     │  │  GTI Alerts API   │
│  Azure Blob/Firestore │  │  (Fetch events)   │
│    (cursor.json)      │  └─────────┬─────────┘
└───────────────────────┘            │
                                     ▼ (Post Cards)
                           ┌───────────────────┐
                           │   Teams Channel   │
                           │ (Adaptive Cards)  │
                           └───────────────────┘
```

### **RSA Implementation Detail**

The notification worker executes through the following sequence:

1. **Authentication**: Authenticates to Azure Bot Service / Teams channel and initializes the GTI API client.  
2. **Cursor Retrieval**: Reads the latest processed timestamp from Azure Blob Storage (`cursor.json`). If no cursor exists, executes an initial backfill sweep.  
3. **Alert Retrieval & Filtering**: Queries GTI alerts created or updated after the cursor timestamp, applying multi-dimensional filters.  
4. **Batching & Formatting**: Groups alerts and converts them into structured Microsoft Teams Adaptive Cards.  
5. **Channel Delivery**: Delivers the Adaptive Cards to the target Teams channel.  
6. **Cursor Checkpoint**: Upon successful delivery, updates and persists the new timestamp cursor back to storage atomically.

### **Alert Filtering and Batching**

#### **Filter Dimensions**

Configurable via environment variables (comma-separated values):

| Filter Dimension | Environment Variable | Default Value | Supported Values |
| :---- | :---- | :---- | :---- |
| **Severity** | `RSA_FILTER_SEVERITY_LEVEL` | `MEDIUM,HIGH` | `LOW, MEDIUM, HIGH, CRITICAL` |
| **Priority** | `RSA_FILTER_PRIORITY_LEVEL` | `MEDIUM,HIGH,CRITICAL` | `LOW, MEDIUM, HIGH, CRITICAL` |
| **Relevance** | `RSA_FILTER_RELEVANCE_LEVEL` | `MEDIUM,HIGH` | `LOW, MEDIUM, HIGH` |
| **Confidence** | `RSA_FILTER_RELEVANCE_CONFIDENCE` | `MEDIUM,HIGH` | `LOW, MEDIUM, HIGH` |

*Filter Logic: Values within a dimension are OR'd; dimensions are AND'd together with the time cursor.*

#### **Batching**

An alert batcher component aggregates alerts and delivers them cleanly:
* Separates alerts within a batch.  
* Saves progress coordinates on successful card posts.  
* Includes visual severity markers (e.g., Critical 🔴, High 🟠, Medium 🟡, Low 🟢).

#### **Alert Formatting**

Each alert card posted to Microsoft Teams contains:
* Alert title, severity indicator, and priority badge.  
* Threat actor / malware family attribution.  
* Affected assets or matched IOC indicators.  
* Direct GTI intelligence console link.  
* Environmental UTC timestamp and alert identifier.

### **State Management**

The incremental cursor state is persisted between scheduler runs:
* **Azure Production Mode** — Stored in Azure Blob Storage (`cursor.json`).  
* **GCP Production Mode** — Stored as a document in Firestore.  
* **Local Development Mode** — Stored in the local filesystem.

## 

## **Deployment (Azure Bicep / Terraform)**

---

The infrastructure is fully automated using Azure Bicep templates for native Azure hosting and Terraform for GCP hosting.

### **Feature Flags & Configuration Parameters**

#### **Azure Native Configuration (`deploy.sh` / Bicep)**

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

#### **GCP Configuration (`terraform.tfvars`)**

| Variable | Required | Default | Description |
| :--- | :---: | :--- | :--- |
| `project_id` | ✅ | | Your Google Cloud Project ID. |
| `region` | ✅ | | The Google Cloud region to deploy to (e.g., `us-central1`). |
| `gti_api_key` | ✅ | | Your Google Threat Intelligence API key. |
| `azure_tenant_id` | ✅ | | The Microsoft Entra ID Tenant ID for bot registration. |
| `azure_subscription_id` | ✅ | | The Microsoft Azure Subscription ID. |
| `rs_alerts_enabled` | | `false` | Set to `true` to deploy the RS Alert poller. |
| `rs_alerts_teams_channel_id` | | | The Teams channel URL/ID for RS Alerts. |

### **Infrastructure Resources**

**Azure Native Stack (Approach B):**

| Resource Name | Azure Type | Purpose |
| :---- | :---- | :---- |
| `bot_function_app` | Microsoft.Web/sites (Flex) | Azure Functions serverless host for FastAPI |
| `bot_key_vault` | Microsoft.KeyVault/vaults | Secure vault for GTI API Key |
| `bot_managed_identity` | Microsoft.ManagedIdentity | Passwordless identity for Key Vault & Bot Service |
| `bot_service` | Microsoft.BotService/botServices | Managed Azure Bot Service proxy for Teams |
| `bot_storage_account` | Microsoft.Storage/storageAccounts | Deployment packages, Teams manifest, & RS cursor |
| `app_insights` | Microsoft.Insights/components | Performance monitoring and distributed tracing |

**GCP Stack (Approach A):**

| Resource Name | Terraform Type | Purpose |
| :---- | :---- | :---- |
| `chat_bot` | google_cloud_run_v2_service | Cloud Run service hosting the container |
| `gti_api_key_secret` | google_secret_manager_secret | Stores the GTI API Key |
| `firestore_db` | google_firestore_database | Cursor state & settings storage |
| `cloud_scheduler` | google_cloud_scheduler_job | Trigger for RS Alert polling |

### **Deployment Procedure**

#### **Approach B: Azure Native Deployment (Target)**

```shell
cd azure
# Set your deployment variables
export GTI_API_KEY="your-gti-api-key"
export RESOURCE_GROUP="gti-teams-bot-rg"
export LOCATION="eastus"

# Execute single-command deployment script
./deploy.sh
```

1. **Launch Template:** The Bicep template provisions the Azure Function App (Flex Consumption), Key Vault, User-Assigned Managed Identity, Storage Account, Application Insights, and Azure Bot resource.  
2. **Secret Ingestion:** The `GTI_API_KEY` is saved into Azure Key Vault and granted access via the Managed Identity.  
3. **Deploy Code:** The backend FastAPI application is packaged and deployed to the Azure Function App.  
4. **Manifest Creation:** The deployment automatically generates the customized Teams App Manifest zip file and places it into the `teams-manifest` storage container.

#### **Approach A: GCP Cloud Run Deployment (Reference)**

```shell
cd gcp/terraform
terraform init

terraform apply \
  -var="project_id=your-gcp-project-id" \
  -var="region=us-central1" \
  -var="gti_api_key=your-gti-api-key" \
  -var="azure_tenant_id=your-azure-tenant-id" \
  -var="azure_subscription_id=your-azure-sub-id"
```

#### **Step: Teams App Manifest Installation**

1. Download the generated `manifest.zip` (from Azure Blob Storage or GCP Cloud Storage).  
2. Open **Microsoft Teams**.  
3. Navigate to **Apps** > **Manage your apps** > **Upload an app** > **Upload a custom app**.  
4. Select `manifest.zip`. The bot is now installed in your organization.  
5. Test the bot in a channel or direct message by sending: `@GTI analyze IP 8.8.8.8`.

## 

## **Limitations**

---

| Limitation | Impact | Mitigation |
| :---- | :---- | :---- |
| **Azure Platform Lock-in** | Approach B relies on Azure-native Flex Consumption and Managed Identities. | Approach A (GCP Cloud Run) is provided for organizations with existing GCP infrastructure. |
| **GTI Session API Latency** | Complex multi-tool agentic queries may take 10–25+ seconds. | Mitigated by immediate progressive placeholder card (`⏳...`) which updates in-place when complete. |
| **Adaptive Card Size Boundaries** | Teams Adaptive Cards have payload limits (~25–30KB). | System prompt instructs GTI to format findings concisely with sub-bulleted details; deep links point to full GTI console. |
| **Stateless Session per Query** | No persistent server-side GTI conversation database. | System prompt includes relevant indicators, keeping queries fast, stateless, and cost-effective. |
| **RSA Notifications is Singleton** | Only one timer worker instance processes alerts per interval. | Prevents duplicate alert deliveries and race conditions on cursor state. |

## 

## **References**

---

* [Google Threat Intelligence (GTI) Documentation](https://gtidocs.virustotal.com/)  
* [GTI Session API — Post a message to a new session](https://gtidocs.virustotal.com/reference/create-session)  
* [Microsoft Teams Developer Platform](https://learn.microsoft.com/en-us/microsoftteams/platform/)  
* [Microsoft Bot Framework SDK Documentation](https://learn.microsoft.com/en-us/azure/bot-service/)  
* [Adaptive Cards Schema & Explorer](https://adaptivecards.io/)  
* [Azure Functions Python Developer Guide (Flex Consumption)](https://learn.microsoft.com/en-us/azure/azure-functions/flex-consumption-plan)  
* [FastAPI Framework Documentation](https://fastapi.tiangolo.com)  
* [GCP Cloud Run Documentation](https://cloud.google.com/run/docs)  
* [Terraform Azure & Google Provider Documentation](https://registry.terraform.io/)  

[image1]: <data:image/png;base64,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>
