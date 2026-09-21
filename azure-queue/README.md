# GTI Teams Bot (Agentic) — Asynchronous Queue Architecture

Two Azure Function Apps replacing the single synchronous bot in
[`azure/azure-bot-function`](../../azure/azure-bot-function) with a
2-tier, queue-decoupled design — so a GTI query that takes 5-10 minutes
never has to fight an HTTP gateway timeout (Bot Framework's own retry
window, or Azure's ingress idle timeout) to get an answer back to the user.

```
Teams user
   │  POST /api/messages
   ▼
┌───────────────────────┐   enqueue job   ┌────────────────────────┐
│  bot-ingest-function   │ ───────────────▶│  Storage Queue          │
│  (HTTP trigger)        │                 │  "gti-query-jobs"       │
│  - verifies JWT        │                 └───────────┬────────────┘
│  - posts placeholder   │                             │ queue trigger
│  - enqueues job, 200s  │                             ▼
└───────────────────────┘                 ┌────────────────────────┐
                                           │  bot-worker-function    │
                                           │  (queue trigger)        │
                                           │  - thread context       │
                                           │  - GTI Agentic query    │
                                           │  - edits/replaces the   │
                                           │    placeholder          │
                                           └────────────────────────┘
```

`bot-ingest-function` always responds to Bot Framework in well under a
second — it never calls the GTI API itself. `bot-worker-function` does the
actual, possibly multi-minute query on its own timeout budget, completely
decoupled from the inbound HTTP request/response cycle. This is what avoids
needing a Premium/long-idle-timeout ingress anywhere in the stack.

## Deploy to Azure

[`bicep/`](bicep) provisions both apps in one deployment — the shared
identity, storage account (job queue, Table Storage sessions, Blob
output-format config), Key Vault, Application Insights, and the Azure Bot
registration, plus the Teams app manifest and each app's code, all fetched
from this repo automatically:

[![Deploy to Azure](https://aka.ms/deploytoazurebutton)](https://portal.azure.com/#create/Microsoft.Template/uri/https%3A%2F%2Fraw.githubusercontent.com%2Favadhsonagara%2Fgti-team-bot%2Fmain%2Fazure-queue%2Fbicep%2Fazuredeploy-button.json)

You'll need a Google Threat Intelligence Agentic API key on hand. Everything
else has a working default — see [`bicep/main.bicep`](bicep/main.bicep) for
full control over every parameter (hosting plans, concurrency, custom
naming, reusing existing resources, etc.) via `az deployment group create`
instead of the button.

After the deployment finishes, grant the identity it created the Graph
permissions listed under "Requirements for both apps" below — the button
does not (and cannot) automate that tenant-admin consent step — then
sideload [`teams-app-manifest/`](gti-teams-bot/teams-app-manifest) into
Teams.

## Why two separate Function Apps (not one app, two triggers)

They could technically live in one Function App, but keeping them separate
lets each run on the hosting plan that actually fits its workload:
`bot-ingest-function` is simple, fast, high-concurrency HTTP traffic — a
plain Consumption plan is fine. `bot-worker-function` runs long, expensive
invocations (deliberately kept few-at-a-time per instance — see host.json's
`batchSize` below, and the `bicep/` deployment's own `workerConcurrentRequests`
parameter) and may need Flex Consumption's longer `functionTimeout` ceiling
depending on `GTI_TIMEOUT_SECONDS`. Scaling and billing them independently
avoids paying for the worker's larger footprint on every inbound webhook call.

## Requirements for both apps

- **Same User-Assigned Managed Identity** (`CLIENT_ID` / `MANAGED_IDENTITY_CLIENT_ID`)
  — both apps authenticate as the one bot; the worker also uses `CLIENT_ID`
  to recognize its own placeholder messages in channel thread history (see
  below).
- **Same underlying storage account** (`AzureWebJobsStorage`) and **same
  queue name** (`JOB_QUEUE_NAME`, default `gti-query-jobs`) — the ingest
  function only ever writes to this queue, the worker only ever reads from
  it.
- The worker's Managed Identity additionally needs these Graph APPLICATION
  permissions (tenant-admin consent), discovered by exercising each scenario
  against a real deployment rather than assumed up front:
  - `ChannelMessage.Read.All` — channel thread context (`app/teams/thread.py`),
    and (below) the channel half of the attachment fallback.
  - `Chat.Read.All` — locating the inbound message in a group chat, for the
    attachment fallback below. Not needed for personal (1:1) chats, which
    have no thread/message-list concept the bot needs Graph for.
  - `Files.ReadWrite.All` — actually downloading a file's bytes via Graph's
    Shares API (`/shares/{token}/driveItem/content`, `app/teams/attachments.py`).
    Per [Microsoft's own docs](https://learn.microsoft.com/en-us/graph/api/shares-get?view=graph-rest-1.0),
    this is the *least*-privileged application permission Graph offers for
    that endpoint — there is no read-only option at the application-permission
    level, even though the bot only ever reads.
  - Attachments (`app/teams/attachments.py`): confirmed live that Bot
    Framework never includes a usable `content_url` on a **channel** message
    at all — not just when a user picks an *existing* SharePoint/OneDrive
    file, but for a freshly-uploaded/pasted one too (Teams channels store
    every file, existing or fresh, in the channel's own SharePoint document
    library either way). Personal (1:1) and group chats get a real,
    directly-usable `content_url` from Bot Framework for a fresh upload —
    only a group chat's *existing*-file-share case needs the same Graph
    fallback as channel always does.
  - Skipping `Files.ReadWrite.All` is a safe choice if that permission is an
    unacceptable trade-off: the code degrades gracefully (proceeds without
    the attachment, GTI is told no file was attached) rather than erroring —
    channel/group-chat attachment analysis just won't work without it.

See each app's `.env.example` for the full settings list.

## How a message flows through the system

1. **`bot-ingest-function`** receives the Bot Framework activity, verifies
   its JWT (`app/teams/auth.py`), and rejects an empty/no-content query
   directly (a usage hint, no queue involved). For a real query, it posts
   the "⏳ Looking into that…" placeholder immediately, builds a small job
   payload (the raw activity JSON + the placeholder's activity id +
   an enqueued-at timestamp — see `app/queue_job.py`), and enqueues it.
   Two safety guards run before enqueueing: a 1 MB raw-body cap on the
   inbound HTTP request (this endpoint is anonymous and publicly
   reachable), and a 48 KiB cap on the serialized job (Storage Queue
   messages are hard-capped at 64 KiB) — either guard fails closed with a
   friendly message to the user instead of a raw error.

2. **`bot-worker-function`** is triggered by the queue. It first checks the
   job isn't stale (`MAX_JOB_AGE_SECONDS`, default 8 min, fixed in `.env` —
   not a Bicep deployment parameter) — a huge backlog
   or repeated retries could otherwise burn a multi-minute GTI query on a
   request the user has long since given up on. It then re-parses the
   activity, downloads any attachments, fetches channel thread context,
   runs the GTI Agentic Sessions pipeline, and delivers the final Adaptive
   Card by editing the placeholder in place (channel) or deleting it and
   sending a fresh message (personal/group chat) — identical delivery
   semantics to the synchronous reference bot.

3. If step 2 fails in a way host.json's `extensions.queues.maxDequeueCount`
   (2) exhausts, the Functions runtime automatically routes the message to
   `gti-query-jobs-poison`, which `bot-worker-function` also listens on —
   best-effort telling the user their request failed instead of leaving the
   placeholder stuck on "looking into that…" forever.

## A real bug this design fixes, not just reproduces

The channel thread-context fetch (`app/teams/thread.py`,
`is_placeholder_message`) has to exclude the bot's own placeholder message
from what gets fed back into the GTI prompt as history — otherwise every
follow-up query in the same thread would show the model its own "⏳ Looking
into that…" text as if a human had said it. Earlier drafts of this filter
(reviewed but not implemented) had a "Check 3 fallback" that matched on the
placeholder text **alone**, with no requirement that the message actually
came from the bot — so a real user pasting or quoting that exact phrase
would have been silently dropped from their own conversation history. This
implementation's fallback (`frm.get("user") is None`, i.e. "no human
sender") is required in addition to the text match, so only the bot's own
posts are ever excluded. See the tests referenced below.

## Worker concurrency: few jobs at a time per instance

`host.json` ships with a conservative `extensions.queues.batchSize: 1` /
`newBatchThreshold: 0` (safe default for a manual/standalone deploy — a
single instance fully finishes or fails its current job before fetching
another). The `bicep/` deployment overrides this at runtime via
`workerConcurrentRequests` (default 15, also driving `PYTHON_THREADPOOL_
THREAD_COUNT`) without editing the deployed code — see
[`bicep/main.bicep`](bicep/main.bicep)'s own comment on that parameter for
why queue-trigger concurrency has no separate ARM-level knob the way HTTP
does. Either way, this bounds concurrency *per instance*, not overall
throughput: Azure Functions still scales out to multiple instances in
parallel as the queue grows, so many users' queries are still processed
concurrently.

## Timeout harmonization

`GTI_TIMEOUT_SECONDS` (worker, default 480s / 8 min, fixed in `.env` — not a
Bicep deployment parameter) and `host.json`'s `functionTimeout` (default
9m30s) are deliberately staggered so a slow-but-still-processing GTI call is
never killed mid-flight by the Functions host. Consumption's
`functionTimeout` has a hard, Azure-enforced 10-minute ceiling; if this app
is deployed on Flex Consumption instead (30-minute ceiling), raise
`GTI_TIMEOUT_SECONDS` and `host.json`'s `functionTimeout` (via the
`AzureFunctionsJobHost__functionTimeout` app setting, so no redeploy is
needed) together.

Protection against a second instance picking up a job while the first is
still working on it does **not** come from `extensions.queues.
visibilityTimeout` — for a Storage Queue trigger, Azure Functions manages
its own internal peek-lock plus automatic lease renewal (fixed, not
configurable) for as long as an instance stays healthy. `visibilityTimeout`
only governs how long a message waits before becoming visible again after
an *explicit* dequeue failure. This repo sets it deliberately short
(`00:00:10`) paired with `maxDequeueCount: 2`, so a genuinely failing job
retries almost immediately and lands on the poison queue quickly, instead
of leaving the user's placeholder stuck for minutes before they're told it
failed.

## A second real bug found on re-review

`bot-worker-function`'s job processor originally re-derived `user_text` from
the raw activity JSON via `.strip()` alone, without also stripping Bot
Framework `<at>...</at>` mention tokens the way `bot-ingest-function`
already did before computing the placeholder's quoted query. That mismatch
meant (a) a query like `<at>Bot</at> what is 1.1.1.1` would have sent GTI a
prompt still containing the raw mention markup, and (b) the placeholder
("> what is 1.1.1.1") and the final delivered response's quoted query would
have visibly disagreed for any message that mentioned the bot. Fixed by
importing and applying the same `strip_mentions()` (already available in
`app/utils/helpers.py`, just not called) before either the quoted query or
the GTI prompt is built — verified with a test asserting the literal
`<at>Bot</at>` token never reaches `gti_client.send_message`.

## What was verified before calling this done

- Both apps' `function_app.py` import cleanly and register their triggers
  correctly under the real `azure-functions` package (a scratch venv with
  each app's actual `requirements.txt` installed).
- The HTTP layer was exercised with real `func.HttpRequest`/`HttpResponse`
  objects (not just the inner helper functions): OPTIONS/GET, an oversized
  body (413), invalid/non-dict JSON (400), a missing auth header (401), a
  non-`message` activity type (200, no-op), and an unhandled exception from
  the ingestion path still resolving to 200 rather than leaking a 500.
- The queue trigger wiring was exercised with a real `func.QueueMessage`
  (not just the inner job-processing function) for both the main queue
  trigger and the poison-queue trigger, confirming `dequeue_count` and
  `get_body()` are read correctly.
- A mocked end-to-end run of `bot-worker-function`'s job pipeline (GTI
  client, Bot Framework Connector calls, Graph, and storage all mocked)
  confirmed: channel messages deliver via edit-in-place, personal/group
  messages deliver via delete-and-repost, and a stale job skips the GTI
  call entirely and notifies the user instead.
- `is_placeholder_message` was exercised directly against five cases: the
  bot's own placeholder (matched by app id), the bot's own placeholder when
  Graph omits `application.id` (matched by "no user" + text), a different
  bot's message containing the same text (correctly NOT excluded), a real
  human quoting the placeholder text verbatim (correctly NOT excluded —
  the fix described above), and an ordinary human message (correctly NOT
  excluded).
- Not yet verified against a real Azurite/Storage Queue or a live Teams
  tenant — that would be the natural next step before a production
  deployment (this task covered application code, not infrastructure/IaC
  for this new layout).

## Application Insights & Observability

Both Function Apps write structured logs to Azure Application Insights with correlation across the entire request lifecycle:

- **Prominent User Query Logging**:
  - `bot-ingest-function` immediately logs:
    `[INGEST 1/3] Inbound User Query | user='<name>' (<id>) scope=<scope> | query='<query>' | attachments=<count>`
  - `bot-worker-function` immediately logs:
    `[WORKER START] Processing User Query | user='<name>' (<id>) scope=<scope> | query='<query>' | dequeue_count=<count> queue_wait=<seconds>s`
- **Application Insights `customDimensions`**:
  `RequestContextFilter` (`app/observability.py`) automatically propagates the following context fields to every log record into `customDimensions`:
  - `query`: The exact user query string.
  - `user_name`: The Teams display name of the user.
  - `user`: The user's Teams / AAD ID.
  - `scope`: Conversation scope (`personal`, `groupChat`, or `channel`).
  - `request_id` / `activity_id`: Bot Framework activity ID correlating the Ingest and Worker invocations.
  - `conversation`: Teams conversation ID.
  - `tenant`: Azure AD tenant ID.
  - `session_id`: GTI Agentic session ID.

### Useful Kusto (KQL) Queries in Application Insights

#### View recent user queries and responses across both apps:
```kusto
traces
| extend user = tostring(customDimensions.user_name),
         query = tostring(customDimensions.query),
         scope = tostring(customDimensions.scope),
         activity_id = tostring(customDimensions.activity_id)
| where message startswith "[INGEST 1/3]" or message startswith "[WORKER START]" or message startswith "[WORKER DONE]"
| project timestamp, cloud_RoleName, message, user, query, scope, activity_id
| order by timestamp desc
```

#### Trace a single query end-to-end (Ingest handoff -> Worker completion):
```kusto
traces
| where customDimensions.activity_id == "<activity-id>" or customDimensions.request_id == "<activity-id>"
| project timestamp, cloud_RoleName, message, customDimensions
| order by timestamp asc
```

## Automated tests

[`gti-teams-bot/tests/`](gti-teams-bot/tests) and [`rs-alerts-function/tests/`](rs-alerts-function/tests) —
each app defines its own top-level `app` package, so the suites are run
independently:

```bash
# Ingest function app tests
pytest gti-teams-bot/tests/ingest

# Worker function app tests
pytest gti-teams-bot/tests/worker

# Cross-app contract & parity tests
pytest gti-teams-bot/tests/cross_app

# RS alerts function tests
pytest rs-alerts-function/tests
```

Covers: inbound JWT auth including the missing-`serviceUrl` rejection, the
`build_job_payload`/`parse_job_payload` wire-format contract between the two
apps, `is_placeholder_message`'s five cases, delivery-failure handling in
`process_job` (including that it now raises instead of silently dropping the
job — see `job_processor.py`'s `DeliveryFailedError`), stale-job handling,
the Bot Framework Connector retry policy (and that `POST`/`send_activity` is
deliberately excluded from it), channel-thread pagination once a thread
exceeds 250 replies, and a parity check that fails if the files meant to
stay byte-identical between the two apps ever diverge.


