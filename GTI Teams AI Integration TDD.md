# **GTI Microsoft Teams Integration**

Technical Design Document

**Table of Contents**

---

[Overview](#overview)

[Problem Statement](#problem-statement)

[Solution](#solution)

[Target Use Cases & Capabilities](#target-use-cases--capabilities)

[2.1 Supported Functional Modules](#21-supported-functional-modules)

[2.2 Example Interaction Patterns](#22-example-interaction-patterns)

[Technical Requirements](#technical-requirements)

[Functional Requirements](#functional-requirements)

[Conversation Support](#conversation-support)

[Data Handling & Response Formatting](#data-handling--response-formatting)

[Non-Functional Requirements](#non-functional-requirements)

[Integration 1: GTI Teams Bot App](#integration-1-gti-teams-bot-app)

[Architecture Diagram](#architecture-diagram)

[Technology Stack](#technology-stack)

[Core Application Components](#core-application-components)

[Security Pipeline](#security-pipeline)

[Why We Don't Publish the Bot to the Public Teams Marketplace](#why-we-dont-publish-the-bot-to-the-public-teams-marketplace)

[Zero-Trust Identity](#zero-trust-identity)

[Observability and Logging](#observability-and-logging)

[Structured Logging](#structured-logging)

[**Integration 2: RSA Notifications**](#integration-2-rsa-notifications)

[RSA Architecture Diagram](#rsa-architecture-diagram)

[RSA Implementation Detail](#rsa-implementation-detail)

[Alert Filtering and Batching](#alert-filtering-and-batching)

[State Management](#state-management)

[Deployment (Bicep/Terraform)](#deployment-bicepterraform)

[Feature Flags](#feature-flags)

[Deployment Procedure](#deployment-procedure)

[Limitations](#limitations)

[References](#references)

# 

## **Overview**

---

The GTI Microsoft Teams AI integration is a conversational threat investigation platform that enables security analysts to access the full capabilities of the Google Threat Intelligence (GTI) Agentic Module directly within Microsoft Teams.

Instead of switching between multiple security tools and manually correlating threat data, analysts can interact with the system using natural language queries such as IOC lookups, reputation checks, malware analysis, threat actor investigations, and intelligence searches.

The Teams application acts as an orchestration layer between Microsoft Teams (via Azure Bot Service) and the GTI Agentic Module — Google's hosted AI system that interprets analyst intent, invokes the appropriate GTI tools, processes complex threat intelligence data, and returns concise, structured, and actionable insights within Teams channels and direct messages.

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
**GTI-Native AI Reasoning**: The system passes user queries directly to GTI's hosted agentic endpoint. GTI autonomously retrieves real-time threat intelligence, executes the appropriate tools, and synthesizes raw data into structured, actionable intelligence summaries formatted as Adaptive Cards.  
**Interactive Thread Context**: Analysts can conduct deep, multi-turn investigations through follow-up queries inside Teams threads.

## 

## **Target Use Cases & Capabilities**

---

The application exposes the comprehensive feature set of the GTI Agentic Module via the GTI Session API.

### **2.1 Supported Functional Modules**

**Collections & Threat Profiles**
* Query active actor campaigns, targeted industries, and strategic intelligence reports.  
* Available Actions:  
  * Retrieve specific collection reports  
  * Threat actor searches  
  * Malware family searches  
  * Vulnerability searches  
  * List configured threat profiles  

**File & Malware Analysis**
* Retrieve dynamic analysis, behavior reports, drop structures, and static parameters for file hashes (SHA256/MD5/SHA1)  

**Intelligence Search**
* Execute advanced queries across standard threat parameters (files, URLs, domains, IPs)

**Network Locations & URLs**
* Perform deep evaluations on IP addresses, domains, and specific URLs  

**Hunting**
* Leverage advanced cross-correlation features to identify active infrastructure patterns  

### **2.2 Example Interaction Patterns**

* **Single-IOC Evaluation:** `@GTI analyze IP 1.1.1.1`
* **Domain Check:** `@GTI is bad-domain.com associated with known campaigns?`
* **Threaded Follow-ups:** Inside a response thread: *"@GTI What file hashes are communicating with this domain?"* followed by *"@GTI summarize the dynamic execution behaviors for the first hash."*

## 

## **Technical Requirements**

---

### **Functional Requirements**

#### **Conversation Support**

* **New Conversations**: Ability to initiate a fresh threat investigation from any authorized Teams channel or direct message using the `@GTI` mention.  
* **Immediate Progress Feedback:** When an analyst submits a query, the bot immediately posts a temporary message (`⏳ Looking into that with Google Threat Intelligence…`). Once the investigation finishes, this message is automatically replaced with the final results card.

#### **Data Handling & Response Formatting**

* **Intelligent Summarization**: GTI's agentic module handles raw tool output synthesis. The bot is responsible for relaying the final GTI response in a clean, readable format.  
* **Structured Response Cards**: Answers are formatted using Microsoft Teams Adaptive Cards, presenting key facts, severity indicators, and clickable links to the full Google Threat Intelligence web portal.

### **Non-Functional Requirements**

* **Security**: The bot only requires a single sensitive credential (GTI API Key), stored securely in Azure Key Vault (or GCP Secret Manager).
* **Performance**: Requests are processed synchronously with built-in retries and exponential backoff to handle temporary network delays smoothly.
* **Scalability**: The backend runs on serverless cloud compute (Azure Functions Flex Consumption or GCP Cloud Run), scaling on demand.

## 

## **Integration 1: GTI Teams Bot App** 

---

The bot backend is built using a modern web framework (FastAPI) running as a serverless ASGI application. It is dynamically wrapped depending on the deployment target (e.g., `azure.functions.AsgiFunctionApp` for Azure, `a2wsgi` for GCP).

### **Architecture Diagram**

**Approach B: Azure Native Hosting (Target Architecture)**
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
```

**Approach A: GCP Hosting (Reference Architecture)**
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
└───────────────────────┘       └───────────────────────┘
            │                               │
            ▼                               ▼
    GTI Agentic API                GTI Agentic API
```

### **Technology Stack**

| Component | Technology | Details |
| :---- | :---- | :---- |
| **Web Framework** | FastAPI \+ ASGI | Core API routing and validation |
| **Deployment (Target)** | Azure Functions (Flex) | Serverless compute scaling on demand |
| **Deployment (Ref)** | GCP Cloud Run | Containerized stateless service |
| **GTI Intelligence** | GTI Session API | POST /agentspace/sessions/{session_id} |
| **Chat Platform** | Microsoft Teams | Azure Bot Service acts as proxy |
| **Secret Management** | Azure Key Vault | Passwordless runtime injection |
| **Infrastructure / IaC** | Azure Bicep / Terraform | Infrastructure-as-Code automation |

### **Core Application Components**

* **Application Entry & Routing Layer:** Manages incoming web requests and connects the Microsoft Teams SDK with the application endpoints.
* **GTI Agent Client:** Manages HTTP communication with Google Threat Intelligence, utilizing connection pooling via FastAPI lifespan events to reduce TCP latency.
* **Adaptive Card Builder:** Assembles structured visual cards that display threat severity tags, key evidence facts, timestamps, and direct web links to the GTI console.

## 

## **Security Pipeline**

---

### **Why We Don't Publish the Bot to the Public Teams Marketplace**

We intentionally do **not** publish this bot as a public, multi-tenant app on the Microsoft Teams Marketplace for critical security reasons:

- **Customer Data Privacy:** A public marketplace app would require a single shared backend server that holds every customer's GTI API keys and processes all internal security queries in one shared environment.
- **Dedicated Cloud Deployment:** Instead, each customer deploys the bot into **their own cloud subscription** (Azure or GCP) using automated infrastructure templates.
- **Total Ownership:** All API keys, chat messages, and threat queries remain entirely inside the customer's own cloud security boundary.

### **Zero-Trust Identity**

The architecture leans heavily on identity-based authentication rather than static passwords. In Azure, the bot authenticates to the Bot Service and Key Vault using a **User-Assigned Managed Identity**. This eliminates the risk of leaked Client Secrets entirely.

## 

## **Observability and Logging**

---

### **Structured Logging**

* **Azure:** Application Insights automatically collects performance metrics, error rates, and end-to-end request tracking IDs.
* **GCP:** Generates JSON logs compatible with GCP Cloud Logging.

## 

## **Integration 2: RSA Notifications** 

---

In addition to interactive questions from analysts, the solution supports an automated **Real-time System (RS) Alerting** workflow. This background task continuously checks Google Threat Intelligence for newly detected security events and posts alert cards into a designated Teams channel.

### **RSA Architecture Diagram**

*(See System Architecture diagram above for poller layout)*

### **RSA Implementation Detail**

1. **Trigger:** A scheduled timer runs at regular intervals (e.g., every 15 minutes).
2. **Fetch Checkpoint:** The service reads the last saved alert timestamp from Azure Blob Storage (or GCP Firestore).
3. **Query GTI Alerts API:** The service calls the GTI alerts API to request new alerts updated since that timestamp.
4. **Filter & Format:** Matching alerts are converted into structured Adaptive Cards.
5. **Publish & Update:** Cards are sent to the Teams channel. After successful delivery, the new checkpoint timestamp is saved atomically.

### **Alert Filtering and Batching**

Allows security teams to precisely tune the alerting frequency and scope using variables such as strict matching on severity, priority, and relevance confidence (e.g., High and Critical only). 

### **State Management**

The incremental cursor state (`updateTime`) is persisted to guarantee alerts are never sent twice.
- **Azure:** Stored in an Azure Storage Account Blob.
- **GCP:** Stored as a document in Firestore.

## 

## **Deployment (Bicep/Terraform)**

---

### **Feature Flags**

| Variable | Description |
| :---- | :---- |
| `ENABLE_RS_ALERTS` | Set to `"true"` to enable the Real-time System (RS) Alert poller. |

### **Deployment Procedure**

#### **Option 1: Azure Native (Target)**
1. Launch the Azure Bicep template in the Azure Portal or via `deploy.sh`.
2. Provide your Function App name, select region, and enter your `GTI_API_KEY`.
3. Azure automatically provisions the Flex Consumption app, Key Vault, and Managed Identity.
4. Download the generated Teams app manifest zip from Blob Storage and upload it to Teams.

#### **Option 2: GCP Cloud Run (Reference)**
1. Configure `terraform.tfvars` with your GCP Project ID, region, and GTI API key.
2. Execute `terraform apply`.
3. Terraform sets up the Entra ID bot registration, GCP Cloud Run services, and Secret Manager.
4. Install the generated Teams app package (.zip) from Cloud Storage into Teams.

## 

## **Limitations**

---

| Limitation | Impact | Mitigation |
| :---- | :---- | :---- |
| **Azure Platform Lock-in** | Approach B relies on Azure Flex Consumption and Managed Identities. | Approach A (GCP Cloud Run) is provided for organizations without Azure footprints. |
| **GTI Session API Latency** | Complex multi-tool agentic queries may take up to 15 seconds. | Handled safely by Bot Framework HTTP timeouts and temporary "loading" messages. |

## 

## **References**

---

* [Google Threat Intelligence (GTI) Documentation](https://gtidocs.virustotal.com/)
* [Microsoft Teams Developer Platform](https://learn.microsoft.com/en-us/microsoftteams/platform/)
* [Azure Functions Flex Consumption](https://learn.microsoft.com/en-us/azure/azure-functions/flex-consumption-plan)
