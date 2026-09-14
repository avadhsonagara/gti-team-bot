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

## Why two separate Function Apps (not one app, two triggers)

They could technically live in one Function App, but keeping them separate
lets each run on the hosting plan that actually fits its workload:
`bot-ingest-function` is simple, fast, high-concurrency HTTP traffic — a
plain Consumption plan is fine. `bot-worker-function` runs long, expensive,
one-at-a-time invocations (see host.json's `batchSize: 1` below) and may
need Flex Consumption's longer `functionTimeout` ceiling depending on
`GTI_TIMEOUT_SECONDS`. Scaling and billing them independently avoids paying
for the worker's larger footprint on every inbound webhook call.

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
  - `ChannelMessage.Read.All` — channel thread context (same as the
    reference bot).
  - `Chat.Read.All` — locating a group-chat message that references an
    *existing* shared file (`app/teams/attachments.py`'s Graph fallback).
    Not needed for personal/1:1 chats (those attachments come straight off
    the Bot Framework activity) or for fresh file uploads in any scope.
  - `Files.ReadWrite.All` — actually downloading that file's bytes via
    Graph's Shares API (`/shares/{token}/driveItem/content`). Per
    [Microsoft's own docs](https://learn.microsoft.com/en-us/graph/api/shares-get?view=graph-rest-1.0),
    this is the *least*-privileged application permission Graph offers for
    that endpoint — there is no read-only option at the application-permission
    level, even though the bot only ever reads. Skipping this permission is a
    safe choice if that's an unacceptable trade-off: the code degrades
    gracefully (proceeds without the attachment, GTI is told no file was
    attached) rather than erroring.

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
   job isn't stale (`MAX_JOB_AGE_SECONDS`, default 15 min) — a huge backlog
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

## Worker concurrency: one job at a time per instance

`host.json`'s `extensions.queues.batchSize: 1` and `newBatchThreshold: 0`
mean a single Function instance fully finishes (or fails) its current job
before fetching another — appropriate since each job can run for several
minutes and there's no benefit to one instance juggling several of them at
once. This does NOT limit overall throughput: Azure Functions still scales
out to multiple instances in parallel as the queue grows, so many users'
queries are still processed concurrently — just one per instance.

## Timeout harmonization

`GTI_TIMEOUT_SECONDS` (worker, default 480s / 8 min), `host.json`'s
`functionTimeout` (default 9m30s), and `extensions.queues.visibilityTimeout`
(default 11 min) are deliberately staggered so a slow-but-still-processing
GTI call is never killed mid-flight by the Functions host, and the queue
never redelivers the same message to a second instance while the first is
still working. Consumption's `functionTimeout` has a hard, Azure-enforced
10-minute ceiling; if this app is deployed on Flex Consumption instead
(30-minute ceiling), raise `GTI_TIMEOUT_SECONDS` and `host.json`'s
`functionTimeout` (via the `AzureFunctionsJobHost__functionTimeout` app
setting, so no redeploy is needed) together.

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
