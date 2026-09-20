# Tests

`bot-ingest-function/`, `bot-worker-function/`, and (one level up, in
`../rs-alerts-function/`) `rs-alerts-function/` each define their own
top-level `app` package (correctly — they're deployed independently, each
with its own `code.zip`, and none imports from the others). That means
`tests/ingest/`, `tests/worker/`, and `../rs-alerts-function/tests/`
**cannot be collected in the same pytest process** — whichever one imports
`app` first "wins", and the others' tests fail with
`ModuleNotFoundError`/`ImportError` for modules that very much do exist,
just under a different app's `app` package.

rs-alerts-function lives outside `gti-teams-bot/` as a standalone app (it
shares no code with the ingest/worker pair), so its tests are colocated with
it at `../rs-alerts-function/tests/` instead of under this `tests/`
directory — everything else about the isolation rule above still applies to
it.

Run each suite separately (each `conftest.py` puts the matching app's root on
`sys.path` for you — no manual `PYTHONPATH` needed):

```bash
pytest tests/ingest                        # bot-ingest-function's app/ tests
pytest tests/worker                        # bot-worker-function's app/ tests
pytest tests/cross_app                     # loads both apps' queue_job.py under private
                                            # module names via importlib (not "app.queue_job"),
                                            # so this one's safe to run standalone or alongside
                                            # any of the above
(cd ../rs-alerts-function && pytest tests) # rs-alerts-function's own app/ tests
```

Or all four in one go:

```bash
./tests/run_all.sh
```

`tests/cross_app/` covers what the two apps' `app` packages can't: the
`build_job_payload`/`parse_job_payload` wire-format contract between them,
and a parity check (`test_shared_module_parity.py`) that fails the moment
the files meant to stay byte-identical between the two apps
(`logging_config.py`, `observability.py`, `teams/activity.py`,
`teams/context.py`, `teams/bot_client.py` — see each app's own copy for why)
ever diverge. There's no shared installable package between the two apps
(see `main.bicep`'s independent `ingestCodeZipUrl`/`workerCodeZipUrl`
deploy-time zips), so this test is what stands in for one — if you edit one
copy, edit the other, or this test tells you so.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install pytest \
  -r bot-ingest-function/requirements.txt \
  -r bot-worker-function/requirements.txt \
  -r ../rs-alerts-function/requirements.txt
```
