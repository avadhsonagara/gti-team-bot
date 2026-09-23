# GTI Teams Bot (Agentic) — GCP Asynchronous Queue Architecture

Two Google Cloud Run functions (2nd Gen) replacing the single synchronous
bot in [`gcp/gcp-bot-function`](../gcp/gcp-bot-function) with a 2-tier,
Pub/Sub-decoupled design — the GCP counterpart to
[`azure-queue`](../azure-queue), built for exactly the same reason: a GTI
query that takes 5-10 minutes must never have to answer the **Bot Framework
Connector's 15-second response deadline** synchronously.

That deadline is a channel protocol constraint, not a hosting-platform
limit — it applies identically no matter how generous the underlying
function's own timeout is. `gcp/gcp-bot-function` is currently configured
for a 30-minute Cloud Functions timeout, which sounds like plenty of
headroom, but is irrelevant to this problem: Bot Framework stops waiting
long before that ever matters. This directory exists to fix that,
end-to-end, the same way `azure-queue` already did for Azure.

```
Teams user
   │  POST /api/messages
   ▼
┌──────────────────────┐   publish job    ┌─────────────────────┐
│  bot-ingest-function  │ ────────────────▶│  Pub/Sub Topic       │
│  (Cloud Run fn, HTTP, │                  │  "<bot>-jobs"        │
│   public)             │                  └──────────┬───────────┘
│  - verifies JWT       │                             │ push (OIDC-authenticated)
│  - posts placeholder  │                             ▼
│  - publishes job, 200 │                  ┌─────────────────────┐
└──────────────────────┘                   │  bot-worker-function │
                                            │  (Cloud Run fn, HTTP,│
                                            │   push-only — locked │
                                            │   to Pub/Sub's push  │
                                            │   identity via IAM,  │
                                            │   never public)      │
                                            │  POST /tasks/process │
                                            │  - thread context    │
                                            │  - GTI Agentic query │
                                            │  - edits/replaces the│
                                            │    placeholder       │
                                            └──────────┬───────────┘
                                                        │ non-2xx response
                                                        │ (up to 5 delivery attempts)
                                                        ▼
                                            ┌─────────────────────┐
                                            │  Dead-letter topic   │
                                            │  "<bot>-jobs-dlq" →  │
                                            │  push → same worker's│
                                            │  POST /tasks/poison  │
                                            │  (tells the user it  │
                                            │   failed)            │
                                            └─────────────────────┘
```

`bot-ingest-function` always responds to Bot Framework in well under a
second — it never calls GTI itself. `bot-worker-function` does the actual,
possibly multi-minute query on its own budget, completely decoupled from
the inbound HTTP request/response cycle.

## Why Pub/Sub push + a manually-declared subscription, not the native Eventarc trigger

Cloud Functions 2nd gen has a built-in Pub/Sub `event_trigger` binding that
looks like the obvious choice — but Eventarc auto-manages its own Pub/Sub
subscription under the hood, and doesn't cleanly expose a
Terraform-attachable `dead_letter_policy` on it (Google's own guidance: use
an HTTP function with your own push subscription when you need dead
lettering). A manually-declared `google_pubsub_topic` +
`google_pubsub_subscription` (push, OIDC-authenticated, with a real
`dead_letter_policy` and `retry_policy`) targeting the worker's HTTPS URL
gives full Terraform control and is the closest analog to Azure Storage
Queue's `maxDequeueCount` + auto-created poison queue. The trade-off is that
the worker becomes a plain authenticated HTTP function instead of a native
CloudEvent trigger — see [`terraform/main.tf`](terraform/main.tf)'s own
comment on this for the full rationale.

## Why standalone, not additive to `gcp/terraform`

This stack provisions its **own** Azure AD app registration, Bot Service,
Secret Manager secrets, and Firestore database — it does not reuse
`gcp/terraform`'s. `gcp/terraform` is already fully self-provisioning in
exactly the same way, and there's no precedent anywhere in this repo for one
Terraform stack consuming a sibling stack's resources (`azure-queue` and
`gcp/` are both independent too). This lets `gcp-queue` be deployed and
validated side by side with `gcp/` before any cutover decision — the same
role `azure-queue` played relative to the old `azure/` tree (which no
longer exists in this repo, having been fully replaced once `azure-queue`
was validated).

If you deploy both stacks into the **same** GCP project, `bot_name` (default
`gti-team-bot-queue` here vs. `gti-team-bot` in `gcp/terraform`) and
`firestore_database` (default `gti-team-bot-queue-db` vs. `gti-team-bot-db`)
are already distinct by default specifically to avoid collisions — service
account IDs and Secret Manager secret IDs are project-unique, and Firestore
supports multiple named databases per project.

## Deploy

```bash
cd terraform
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars: project_id, gti_api_key, azure_resource_group_name
terraform init
terraform apply
```

This provisions both Cloud Run functions (built directly from source — no
Docker image to build by hand, Cloud Build handles it via the Python
buildpack whenever the uploaded source zip's content hash changes), the
Pub/Sub topics/subscriptions, Firestore, Secret Manager, IAM, and the Azure
Bot Service + Teams channel registration, in one `terraform apply`. See
[`terraform/main.tf`](terraform/main.tf) for full control over every
parameter, and `terraform apply`'s own `post_deployment_instructions`
output for the manual steps still required afterward (sideloading the Teams
manifest, granting the Graph `ChannelMessage.Read.All` tenant-admin
consent — this repo's Managed-Identity-only design means that consent step
can never be automated, confirmed against Microsoft's own docs).

## Requirements for both functions

- **Same Microsoft Entra app registration** (`CLIENT_ID`/`CLIENT_SECRET`/
  `TENANT_ID`) — both functions authenticate as the one bot; the worker also
  uses `CLIENT_ID` to recognize its own placeholder messages in channel
  thread history (see below).
- **Same Pub/Sub topic** — the ingest function only ever publishes to it,
  the worker only ever receives pushes from its subscription. Wired
  entirely through Terraform (the push subscription's `push_endpoint`); the
  worker's own code never references a topic or subscription name at all.
- The worker additionally needs the Graph APPLICATION permissions
  `ChannelMessage.Read.All` (channel thread context, `app/teams/thread.py`)
  and `Chat.Read.All` / `Files.ReadWrite.All` for the attachment fallback —
  same requirements as `gcp/gcp-bot-function`, since `app/teams/attachments.py`
  and `app/teams/thread.py` are reused unchanged from there. See each
  function's `.env.example` for the full settings list.

## The timeout budget — the one hard platform ceiling this design lives under

A Pub/Sub **push** subscription's `ack_deadline_seconds` maxes out at **600
seconds (10 minutes)**, with **no lease-renewal mechanism available to a
push endpoint** — that API exists only for pull subscribers. This is a real
platform ceiling, not a tuning choice: unlike Azure Storage Queue, whose
Functions trigger has automatic peek-lock plus lease renewal for as long as
an instance stays healthy (see `azure-queue/README.md`'s own note on this),
there is nothing here that can extend the runway past 600s no matter how
the worker is configured.

Everything in this design budgets against that ceiling explicitly:
- `terraform/main.tf`'s `job_subscription.ack_deadline_seconds` is
  hardcoded to `600` — **not left to its default**. The Terraform provider's
  own docs confirm an omitted `ack_deadline_seconds` silently defaults to
  **10 seconds**, which would redeliver every real job to a second instance
  ten seconds after the first started working on it. This was caught during
  design review before ever being deployed, not discovered in production.
- `worker_timeout_seconds` (Terraform variable, default `600`) matches that
  ceiling exactly and is validated to never exceed it — nothing past 600s is
  ever reachable regardless of what the Cloud Function's own timeout allows.
- `GTI_TIMEOUT_SECONDS` (worker, default 480s / 8 min — same default
  `azure-queue` uses) leaves roughly 120s of headroom under the 600s
  ceiling for attachment downloads, thread-context fetch, delivery, and
  cold start.
- `worker_min_instances` defaults to `1` (not `0`, unlike a typical
  scale-to-zero setup) specifically because a cold start now competes
  directly against that same fixed budget in a way it never did for the old
  synchronous `gti-bot`'s much more forgiving timeout.

If this budget ever proves too tight in practice, the fix is not a bigger
timeout — 600s is a true ceiling with no override — it's a different
delivery model entirely (pull-based workers, Cloud Tasks, etc.), out of
scope for this design.

## Redelivery and the duplicate-processing guard

Pub/Sub push is **at-least-once by design**. Combined with the fixed ack
ceiling above, this creates a failure mode Azure's design structurally
cannot have: if the worker doesn't respond inside `ack_deadline_seconds` —
a slow GTI call, a slow cold start, or an ack-deadline misconfiguration —
Pub/Sub redelivers the *same* message, possibly to a second, concurrent
instance, while the first is still running.

`app/dedup_store.py` closes this gap with a Firestore
[`create()`](https://cloud.google.com/firestore)-based claim keyed on the
Pub/Sub push envelope's `messageId` (stable across redeliveries of the same
logical message, unlike an in-memory guard — Cloud Functions gen2 can scale
to multiple instances with independent memory, so gcp-bot-function's own
in-memory `_claim_activity` dedup approach wouldn't help here even though it
solves an analogous problem on the synchronous app). Firestore is already a
hard dependency (`session_store.py`, `output_format_store.py`), so this adds
no new infrastructure. The claim fails **open** (lets the job proceed) if
Firestore itself is unreachable — a missed dedup check is far less harmful
than dropping a real user's request. Configure a Firestore TTL policy on
the `message_dedup::*` documents in `firestore_bot_config_collection` so old
claims expire on their own instead of accumulating forever.

## How a message flows through the system

1. **`bot-ingest-function`** receives the Bot Framework activity, verifies
   its JWT (`app/teams/auth.py`), and rejects an empty/no-content query
   directly (a usage hint, no Pub/Sub involved). For a real query, it posts
   the "⏳ Looking into that…" placeholder immediately, builds a small job
   payload (the raw activity JSON + the placeholder's activity id + an
   enqueued-at timestamp — see `app/queue_job.py`), and publishes it. Two
   safety guards run before publishing: a 1 MB raw-body cap on the inbound
   HTTP request (this endpoint is publicly reachable), and a payload-size
   guard before publish (`MAX_JOB_PAYLOAD_BYTES`, default 48 KiB — kept at
   the same magnitude as Azure's for behavioral consistency, even though
   Pub/Sub's real ceiling is 10 MB; this is a deliberate app-level guard
   against pathologically large jobs, not a platform limit) — either guard
   fails closed with a friendly message to the user instead of a raw error.

2. **`bot-worker-function`**'s `/tasks/process` route is pushed to by the
   job subscription. It claims the message's Pub/Sub `messageId` (see
   above), checks the job isn't stale (`MAX_JOB_AGE_SECONDS`, default 8
   min — a large backlog or a redelivered job could otherwise burn a
   multi-minute GTI query on a request the user has long since given up
   on), then re-parses the activity, downloads any attachments, fetches
   channel thread context (excluding its own already-posted placeholder —
   see below), runs the GTI Agentic Sessions pipeline, and delivers the
   final Adaptive Card by editing the placeholder in place (channel) or
   deleting it and sending a fresh message (personal/group chat) —
   identical delivery semantics to `gcp/gcp-bot-function`.

3. **HTTP status code is the retry signal Pub/Sub actually acts on** — there
   is no exception-based signaling visible to Pub/Sub the way Azure's queue
   trigger has. `main.py`'s `/tasks/process` handler maps
   `app/job_processor.py`'s exception taxonomy explicitly:

   | Outcome | HTTP status | Pub/Sub behavior |
   |---|---|---|
   | Named `GTI*Error` (friendly card delivered) | 200 | Acked, no redelivery |
   | Stale job / empty query | 200 | Acked, no redelivery |
   | Another delivery already claimed this `messageId` | 200 | Acked, no redelivery — the delivery holding the claim handles it |
   | `DeliveryFailedError` (all delivery fallbacks exhausted) | 500 | Redelivered per `retry_policy` |
   | Unexpected `Exception` | 500 | Redelivered per `retry_policy` |
   | Malformed/unparseable job payload | 500 | Redelivered — same reasoning as above: it'll fail identically every time and needs to reach the dead-letter topic, not be silently dropped with a 400 |

4. If step 3 returns 500 enough times to exhaust `max_delivery_attempts`
   (Terraform variable, default **5** — Pub/Sub's own platform floor; there
   is no way to configure this lower, unlike Azure's `maxDequeueCount: 2`),
   Pub/Sub routes the message to the dead-letter topic, whose own push
   subscription hits the same worker's `/tasks/poison` route —
   best-effort telling the user their request failed instead of leaving the
   placeholder stuck on "looking into that…" forever. There is no
   poison-of-poison: a failure inside `/tasks/poison` itself is just
   logged and still acked with 200 (nowhere further to send it), matching
   Azure's design exactly.

## The bot's-own-placeholder exclusion — carried over from `azure-queue`, not present in `gcp/gcp-bot-function`

`gcp/gcp-bot-function`'s synchronous design fetches channel thread context
*before* posting its own placeholder, so ordering alone keeps the bot from
reading its own "looking into that…" text back as if a human had said it.
That trick stops working once ingest and worker are two separately-deployed
functions: the placeholder is already posted by the time the worker ever
runs. `app/teams/thread.py::is_placeholder_message` — ported from
`azure-queue`, which hit and documented this exact bug — filters it out
explicitly instead, matching on the placeholder text **and** (an
application-id match, **or** — when Graph omits `application.id` — the
absence of a human sender). Text alone is deliberately not sufficient: a
real user quoting or pasting that exact phrase must never be silently
dropped from their own conversation history.

## A second fix carried over from the same file

`get_thread_context()` previously (in `gcp/gcp-bot-function`, and in this
directory's own first draft) resolved the Graph thread id via
`get_thread_root_id(activity.conversation.id)` directly — which has no
fallback for a channel thread's own **opening** post, since only a *reply*'s
`conversation.id` carries the `;messageid=` suffix that regex looks for.
Fixed by reusing `get_session_key()` — already present for GTI session
continuity, and already correct (root-id-or-own-activity-id fallback) —
instead of recomputing an unfallback'd version. Regression-tested in
[`tests/worker/test_thread_context_fix.py`](tests/worker/test_thread_context_fix.py).

## What was verified before calling this done

- Both functions' `main.py` import cleanly under their own actual
  `requirements.txt`, in isolated virtualenvs.
- `terraform validate` (after `terraform init -backend=false`) passes clean
  against the real `hashicorp/google` provider schema — this is what caught
  the `ack_deadline_seconds` default-value risk and confirmed the
  `dead_letter_policy`/`push_config`/`oidc_token` block shapes before ever
  being deployed, rather than discovering a typo at `apply` time.
- 90 automated tests pass across all three suites (see below) — including
  the full HTTP status-code mapping table above, exercised end-to-end
  through a real Flask test client (not just `process_job()` in isolation),
  the five `is_placeholder_message()` scenarios, the thread-id fallback fix,
  the Pub/Sub push envelope unwrap (valid, malformed, non-base64, non-JSON),
  and the Firestore messageId claim (first delivery proceeds, a simulated
  redelivery is skipped, an unreachable Firestore fails open).
- Not yet verified against a real Pub/Sub topic/subscription or a live
  Teams tenant — that's the natural next step before a production
  deployment (this covered application code and Terraform schema
  correctness, not a live end-to-end run).

## Automated tests

Each function defines its own top-level `app` package — same reason
`azure-queue` runs its suites independently rather than as one combined
`pytest` invocation: `bot-ingest-function/app/config.py` and
`bot-worker-function/app/config.py` are different modules with the same
dotted name, and having both on `sys.path` at once makes `import app.config`
ambiguous.

```bash
# Ingest function tests (from bot-ingest-function's own venv/requirements.txt)
pytest tests/ingest

# Worker function tests (from bot-worker-function's own venv/requirements.txt)
pytest tests/worker

# Cross-app contract & parity tests — needs BOTH apps' dependencies
# installed in the same environment (e.g. a third venv with both
# requirements.txt files installed), since it loads both apps' app.queue_job
# in one process.
pytest tests/cross_app
```

Covers: inbound JWT auth (byte-identical logic to `gcp/gcp-bot-function`'s,
already-proven test suite reused directly), the `build_job_payload`/
`unwrap_pubsub_envelope`/`parse_job_payload` wire-format contract between the
two functions, `is_placeholder_message`'s scenarios, the thread-id
resolution fix, `job_processor.py`'s exception taxonomy and the HTTP
status-code mapping `main.py` applies to it, the Firestore messageId dedup
claim, `poison_handler.py`'s dead-letter notification (including that it
skips `installationUpdateRemove` cleanup jobs, which have no conversation
left to notify), and a parity check that fails if the files meant to stay
byte-identical between the two functions (`logging_config.py`,
`observability.py`, `teams/activity.py`, `teams/context.py`,
`teams/bot_client.py`, and the `PLACEHOLDER_TEXT` constant specifically)
ever diverge.

## Observability

Both functions write structured JSON logs to Google Cloud Logging
(`app/logging_config.py`, reused unchanged from `gcp/gcp-bot-function`) with
Cloud Trace correlation (`app/observability.py`). Look for:

- `bot-ingest-function`: `[INGEST 1/3] Inbound User Query`, `[INGEST 3/3]
  Published to <topic>`.
- `bot-worker-function`: `[WORKER START] Processing User Query`, `[WORKER
  DONE] Job finished successfully` / `Delivery failed`.
- `[DEDUP] messageId=... already claimed` — a redelivery was correctly
  skipped rather than double-processed.
- `[POISON] Job permanently failed after exhausting delivery attempts` — a
  job hit `max_delivery_attempts` and landed on the dead-letter topic.
