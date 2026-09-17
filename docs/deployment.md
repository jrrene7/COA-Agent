# Deployment Strategy

This document covers how the Customer Outreach Automation Agent is packaged,
configured, promoted between environments, observed in production, and rolled
back. It reflects the system as built: two synchronous CLI pipelines — outbound
lead outreach and inbound message triage — over a shared durable SQLite run
store.

## Current shape and its limits

The agent is a **batch CLI process**, not a service. One invocation processes one
lead (outbound) or one message (inbound) and exits. There is no HTTP surface, no
scheduler, and no concurrency inside the process.

The two pipelines have genuinely different deployment profiles, and this matters
more than it first appears:

- **Outbound is batch-shaped.** Leads are processed on a schedule; nobody is
  waiting. A nightly or hourly job is the right fit.
- **Inbound is latency-sensitive.** A customer is waiting, and triage exists to
  get an angry message in front of a human quickly. Running it on a 15-minute
  cron defeats its purpose — an escalation that surfaces 15 minutes late is
  barely better than no escalation. Inbound wants a queue worker consuming
  messages as they arrive.

Ship outbound as a scheduled job first; give inbound a queue worker as soon as
it handles real traffic.

| Target | Fit | Notes |
| --- | --- | --- |
| Scheduled container job (ECS Scheduled Task, Cloud Run Job, K8s CronJob) | **Recommended** | Matches the run-to-completion model exactly |
| Queue-driven worker | **Recommended for inbound** | One message per job; the wrapper loop calls `InboundOrchestrator.run` per message |
| Long-running HTTP service | Poor fit today | Would need a request/response layer and async handling the pipeline lacks |
| Serverless function | Poor fit | Runs routinely exceed short function timeouts because of LLM latency and retries |

Outbound starts on the scheduled container job and moves to a queue worker when
lead volume justifies parallelism. Inbound should be queue-driven from the point
it sees real customers, for the latency reason above.

## Packaging

Build a pinned image rather than installing at deploy time.

```dockerfile
FROM python:3.13-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY agents/ agents/
COPY lib/ lib/
COPY tools/ tools/
COPY main.py .

RUN useradd --create-home --uid 10001 agent \
    && mkdir -p /data/output /data/runs \
    && chown -R agent /data
USER agent

VOLUME ["/data"]
ENTRYPOINT ["python", "main.py"]
```

Requirements:

- **Pin the base image by digest** in production, not by tag.
- **Run as non-root.** Reports are written `0600` and the run database `0600`;
  both assume a stable, non-shared UID.
- **Do not bake `config.env` into the image.** Secrets are injected at runtime.
- Tag images with the git SHA. `latest` is not deployable.

## Configuration and secrets

| Variable | Required | Purpose |
| --- | --- | --- |
| `OPENAI_API_KEY` | yes | LLM access; absent, `LLM.__init__` raises immediately |
| `TAVILY_API_KEY` | yes | Web search; absent, `web_search` degrades to an error payload rather than crashing |
| `OPENAI_BASE_URL` | no | Defaults to the public API; set for a proxy or gateway |
| `COA_LOG_LEVEL` | no | Defaults to `INFO` |

Locally these come from `config.env` (gitignored). In every deployed
environment they must come from a secret manager — AWS Secrets Manager, GCP
Secret Manager, or Kubernetes Secrets — injected as environment variables at
container start. `load_dotenv("config.env")` is a no-op when the file is absent,
so no code change is needed between local and deployed runs.

**Never** mount `config.env` into an image or commit it. `.gitignore` covers
`*.env`, but that is a safety net, not the control.

Log output is scrubbed by `RedactingFilter`/`RedactingFormatter` before it
leaves the process, so shipping stdout/stderr to a log aggregator does not leak
keys. This is defense in depth, not permission to log secrets deliberately.

## Environments

Three environments, promoted in order:

1. **Local** — `config.env`, run store at `./runs/runs.db`, reports in `./output/`.
2. **Staging** — real API keys against a low spend cap, a fixed list of ~20 known
   leads, and a persistent volume. Every deploy runs this list and its KPI
   summary is compared against the previous build (see below), and runs the
   evaluator evals (`python -m evals.run`).
3. **Production** — real leads, alerting enabled, volume backed up.

Promotion is by image digest. The artifact that passed staging is the artifact
that reaches production; nothing is rebuilt between the two.

## State and storage

Two durable artifacts, both containing lead data:

- `runs/runs.db` — SQLite run store, keyed by `run_id`, both directions
- `output/*.md` — generated lead reports and inbound triage notes

Both need a **persistent volume**. On an ephemeral container filesystem they
vanish on exit, which defeats the point of the run store.

SQLite constraints that shape the topology:

- **One writer.** SQLite does not support concurrent writers across containers.
  A single scheduled job is fine. The moment you run parallel workers, either
  give each worker its own database file and aggregate for reporting, or move
  the store to Postgres behind the same `RunStore` interface.
- **The volume must be block storage, not NFS/EFS.** SQLite locking over network
  filesystems is unreliable and corrupts under contention.
- Back up by copying the database file while no run is in flight, or use
  `sqlite3 .backup`. Retention should match your lead-data policy — the file
  contains scraped company intelligence and unredacted contact details.

## Health, observability, and KPIs

The pipeline already emits structured, `run_id`-tagged logs. Ship stdout to the
aggregator and alert on these events:

| Signal | Source | Alert when |
| --- | --- | --- |
| `pipeline_step_incomplete` | research gate | Rate exceeds ~10% of runs — usually scraping is blocked or the site changed |
| `pipeline_feedback_loop` | evaluator gate | Rate rises sharply — usually a prompt or model regression |
| `pipeline_escalated` | outbound routing | Rate rises — draft quality is falling |
| `inbound_escalated` | inbound routing | **Any occurrence needs a human within SLA**, and a rate spike means something is wrong with the product, not the pipeline |
| `pipeline_step_retry` | `_run_with_retry` | Sustained increase — upstream instability or schema drift |
| Non-zero exit | `main.py` | Any occurrence in production |
| `estimated_cost_usd` | run store | Daily total exceeds budget |

`inbound_escalated` is the one signal here that is not really about pipeline
health. It means a real customer is angry or leaving, and it needs routing to a
human on-call path, not just a dashboard.

`python main.py --kpis` prints the aggregate summary from the run store. Wire it
into a daily scheduled job and publish the result, or query the database
directly:

```sql
SELECT date(started_at) AS day,
       direction,
       count(*)                                  AS runs,
       sum(status = 'completed')                 AS completed,
       sum(status = 'incomplete')                AS incomplete,
       sum(status = 'failed')                    AS failed,
       sum(feedback_loops > 0)                   AS needed_retry,
       sum(escalations > 0)                      AS escalated,
       sum(total_tokens)                         AS tokens,
       round(sum(estimated_cost_usd), 4)         AS cost_usd,
       round(avg(duration_seconds), 2)           AS avg_seconds
FROM runs
GROUP BY day, direction
ORDER BY day DESC;
```

The KPIs worth watching, and what a regression in each usually means:

- **Success rate** (`completed / runs`) — the headline number.
- **Incomplete rate** — research gate aborts. Rising means scraping or search is
  failing, not that the model got worse.
- **Feedback-loop rate** — how often the evaluator rejected a draft. This is the
  clearest early signal of a prompt or model regression.
- **Escalation rate**, split by direction. Outbound escalation rising means draft
  quality is falling. Inbound escalation rising means customer sentiment is
  falling — a product signal, not a pipeline one. Conflating the two hides both.
- **Queue distribution** (`by_queue`) — whether routing is actually spreading work
  or funnelling everything into one queue, which usually means a threshold is
  mistuned.
- **Sentiment distribution** (`by_sentiment`) — the trend line matters more than
  any single run.
- **Step retry count** — transient instability and schema drift.
- **Tokens and estimated cost per run** — cost control, and a leading indicator
  of prompt bloat or runaway tool loops.
- **Average duration, and per-step duration** in `step_metrics` — tells you
  which stage a slowdown belongs to.

Cost figures come from a hardcoded price table in `lib/kpi.py`. It is
approximate and must be updated when provider pricing changes; treat it as a
relative trend line, not an invoice.

## Deploy sequence

1. CI runs `python -m pytest` on every push. The suite makes no network calls, so
   it needs no credentials.
2. Build and tag the image with the git SHA.
3. **Run `python -m evals.run` against the build.** This calls the real model, so
   it needs credentials and a small budget — run it in a staging-credentialed CI
   job, not the unit-test job. It exits non-zero if the evaluator would have
   approved a draft a human labelled "review".
4. Deploy to staging. Run the fixed lead list.
5. Compare the staging KPI summary against the previous build. Block promotion
   on a material drop in success rate, a jump in feedback-loop or escalation
   rate, or an unexplained rise in cost per run.
6. Promote the same digest to production.
7. Watch the first production runs before considering the deploy done.

Steps 3 and 5 are the ones that matter most, and they catch different things.
Because the pipeline's output is generated text, a regression will not
necessarily throw an exception — the unit suite can stay green while quality
falls off a cliff.

- **Step 3 catches a broken judge.** The evaluator is now the gate that decides
  what reaches a prospect, and it is itself a model call. A prompt edit or a
  model version bump can quietly make it lenient, at which point every other
  quality signal still looks healthy while bad drafts sail through.
- **Step 5 catches a broken generator.** Rising feedback-loop and escalation
  rates mean drafts are getting worse, even when the evaluator is working fine.

Run the evals whenever you change the evaluator prompt, the scoring thresholds
in `lib/routing.py`, or the model — those are the three inputs that move the
gate.

## Rollback

Deploys are stateless, so rollback is redeploying the previous image digest. No
migration is required: `RunStore` creates its tables with `IF NOT EXISTS`, and
new columns added by a future version are simply unread by an older one.

Two caveats:

- If a future schema change **drops or renames** a column, rollback stops being
  safe. Additive changes only, or ship an explicit migration path.
- Rollback does not undo reports already written. If a bad build produced
  incorrect outreach, identify the affected runs by `run_id` and time window and
  discard those reports deliberately:

```sql
SELECT run_id, company, report_path FROM runs
WHERE started_at BETWEEN ? AND ?;
```

## Hardening before real production use

Known gaps, roughly in priority order:

1. **Nothing enforces the "draft only" guarantee outside this codebase.** The
   pipeline never sends anything, but the reports it writes contain send-ready
   text. Whatever consumes `output/` must respect the `escalate` flag and the
   DRAFT marker, or the human-review gate is decorative.
2. **No SLA tracking on escalations.** Inbound escalation records who owns a
   message but nothing measures whether they actually responded. That is the
   next thing to build if inbound handles real traffic.
3. **No rate limiting across runs.** Concurrent jobs can collectively breach
   provider rate limits even though each run retries politely on its own.
4. **No spend cap.** Nothing stops a pathological run from consuming tokens up to
   the tool-iteration ceiling. Add a per-run token budget checked in `LLM.invoke`.
5. **`scrape_website` has no domain allowlist or SSRF protection beyond the
   scheme check.** It blocks non-`http(s)` URLs but will still fetch private
   address ranges. Add an allowlist or an egress proxy before pointing it at
   model-chosen URLs in production.
6. **SQLite single-writer ceiling**, as above.
7. **No PII retention policy on the run store.** Reports are PII-redacted;
   snapshots deliberately are not, so they can reconstruct a run faithfully. Set
   a retention window and prune.
