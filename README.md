# Customer Outreach Agent

A spec-driven multi-agent system with two pipelines over a shared spine:

- **Outbound** — researches a sales lead, crafts personalised outreach, scores the draft for quality and tone, and routes it to a queue or a human reviewer.
- **Inbound** — triages a customer message for sentiment, intent and urgency, routes it to an owner, and either drafts a reply or escalates it with a handoff brief.

Nothing is ever sent automatically: outbound drafts are reviewed or queued, inbound replies are drafts, and escalated messages get no draft at all. LangGraph manages workflow state, LangChain Core provides message primitives, and application logs are redacted before emission.

## SDD Submission Checklist

This project follows the SDD flow: constitution, specification, plan, tasks, implementation, tests, and validation.

| Requirement | Location |
|---|---|
| SDD specs | `constitution.md`, `specs/outreach-pipeline.md`, `plans/outreach-pipeline-plan.md`, `tasks/outreach-pipeline-tasks.md` |
| README | `README.md` |
| Unit tests | `tests/` |
| Evaluator evals | `evals/` (`python -m evals.run`) |
| Architecture diagram | `docs/architecture.md` |
| Deployment strategy | `docs/deployment.md` |
| Source code | `main.py`, `agents/`, `lib/`, `tools/` |

## Architecture

See `docs/architecture.md` for the Mermaid architecture diagram.

```
OUTBOUND                              INBOUND
Lead (company + URL)                  Customer message
        │                                     │
  Research Agent                        Triage Agent
        │                               (sentiment/intent/urgency)
  profile complete? ──no──> END               │
        │ yes                                 │
  Marketing Agent ◄────────┐                  │
        │                  │ revise           │
  Sales Agent              │ (+critique)      │
        │                  │                  │
  Evaluator Agent ─────────┘                  │
  (sentiment, scores, verdict)                │
        │ approve / budget spent              │
        └──────────► lib.routing ◄────────────┘
                 (deterministic: signals → decision)
                          │
         ┌────────────────┴────────────────┐
     standard / priority            human_review / escalation
         │                                  │
   ReportWriter / HandoffWriter (redacts PII, chmod 0600)
                          │
                  output/*.md
```

Every run is persisted to a durable SQLite store keyed by `run_id`
(`runs/runs.db`, chmod 0600) alongside per-step metrics and token/cost KPIs.
Run `python main.py --kpis` for the aggregate summary, broken down by direction,
queue, and sentiment.

```bash
python main.py --company "Acme" --url https://acme.example      # outbound
python main.py --mode inbound --sender a@b.com --body -         # inbound (stdin)
python main.py --kpis                                            # KPI summary

python -m pytest                                                 # unit tests, no API calls
python -m evals.run                                              # evaluator evals, real API calls
```

### Evaluating the evaluator

The outbound quality gate is itself a model call, so `evals/` holds a corpus of
labelled drafts — good ones, plus deliberate failure modes (generic, hallucinated
facts, multiple CTAs, no CTA, wrong tone). `python -m evals.run` scores the real
`EvaluatorAgent` against them and fails if it would have approved a draft a human
labelled "review". Approving a bad draft sends a real email; over-flagging a good
one costs a reviewer a minute, so the two are measured separately rather than
blended into one accuracy number.

| Agent | Responsibility | Output |
|---|---|---|
| **Research** | Scrapes website + web search for company intel | `research_data` |
| **Marketing** | ICP fit, value props, tone, competitors | `marketing_data` |
| **Sales** | Cold email, LinkedIn note, follow-ups, objections | `sales_data` |
| **ReportWriter** | Assembles Markdown report | `report_path` |

## Project Structure

```
COA-Agent/
├── constitution.md        # Durable SDD project rules
├── specs/                 # SDD requirements and acceptance criteria
├── plans/                 # SDD engineering plan and traceability matrix
├── tasks/                 # SDD executable task list
├── docs/
│   └── architecture.md    # Mermaid architecture diagram
├── agents/
│   ├── state.py           # OutreachState TypedDict
│   ├── research_agent.py  # ResearchAgent
│   ├── marketing_agent.py # MarketingAgent
│   ├── sales_agent.py     # SalesAgent
│   ├── report_writer.py   # ReportWriter
│   └── orchestrator.py    # Orchestrator (LangGraph workflow)
├── lib/
│   ├── llm.py             # OpenAI wrapper with retry logic
│   ├── messages.py        # LangChain message helpers
│   ├── logging_config.py  # Redacted logging configuration
│   ├── workflow.py        # Run and Snapshot result objects
│   ├── tooling.py         # @tool decorator + Tool class
│   └── memory.py          # ShortTermMemory
├── tools/
│   ├── web_tools.py       # web_search, scrape_website
│   └── file_tools.py      # write_report
├── output/                # Generated .md reports land here (git-ignored)
├── config.env             # API keys — DO NOT COMMIT
├── requirements.txt
├── main.py                # Single-lead entry point
├── main_batch.py          # Batch entry point
├── tests/                 # Offline unit tests
└── README.md
```

## Setup

### 1. Clone and create a virtual environment

```bash
git clone <repo-url>
cd COA-Agent
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Add your API keys

Open `config.env` and fill in your keys:

```env
OPENAI_API_KEY=sk-...
TAVILY_API_KEY=tvly-...
OPENAI_BASE_URL=https://api.openai.com/v1
```

> **Never commit `config.env`** — it is listed in `.gitignore`.

You need:
- **OpenAI API key** — [platform.openai.com](https://platform.openai.com)
- **Tavily API key** — [tavily.com](https://tavily.com) (free tier available)

## Usage

### Single lead

```bash
python main.py
```

Edit the `company` and `url` values in `main.py` to target a different lead.

### Batch processing

Edit the `LEADS` list in `main_batch.py`, then run:

```bash
python main_batch.py
```

A `output/batch_summary.json` file is written with status for every lead.

## Testing

Run the offline unit tests with:

```bash
python -m unittest discover
```

The unit tests stub external API packages where needed, so they do not require OpenAI or Tavily credentials.

### Import as a library

```python
from agents.orchestrator import Orchestrator

orchestrator = Orchestrator(model="gpt-4o-mini", verbose=True)
run = orchestrator.run(company="Acme Corp", url="https://acme.com")
state = run.get_final_state()

print(state["report_path"])
print(state["marketing_data"]["icp_fit"])
print(state["sales_data"]["email"]["subject"])
```

### Inspect intermediate results

```python
for snap in run.snapshots:
    print(f"Step: {snap.step_id} — keys: {list(snap.state_data.keys())}")
```

## Output

Each run produces a Markdown file in `output/`:

```
output/stripe_lead_report.md
```

The report contains:
1. **Company Profile** — summary, news, key people, tech stack, pain points
2. **Marketing Strategy** — ICP fit score, value propositions, tone, hook, competitors
3. **Sales Outreach** — cold email, LinkedIn note, follow-up sequence
4. **Objection Handling** — top 3 objections with responses
5. **Recommended Next Action**

## Cost

Approximately **$0.03–$0.08 per lead** with `gpt-4o-mini`. Switch to `gpt-4o` in the `Orchestrator` constructor for higher-quality output.

## Extending the Pipeline

| Extension | How |
|---|---|
| Email sending (SendGrid) | Add `send_email` tool to `tools/`; call after `SalesAgent` |
| CRM logging (HubSpot) | Add `hubspot_create_contact` tool; add `crm_step` in `Orchestrator` |
| Slack notifications | Add `slack_notify` tool; call at end of `report_step` |
| Web UI | Expose `Orchestrator.run()` via a FastAPI endpoint |

## Security Notes

- API keys are loaded from `config.env` via `python-dotenv` — never hardcoded.
- `scrape_website` validates URL scheme (only `http`/`https`) and blocks non-web schemes.
- `write_report` sanitises filenames, prevents path traversal, and `chmod`s each report `0600` (owner read/write only) since reports carry lead PII.
- `redact_pii()` strips incidental emails, phone numbers, and SSN-like patterns out of the assembled report before it's written — decision-maker names/titles are left intact, since those are the report's purpose.
- `configure_logging()` redacts API keys, bearer tokens, passwords, and common secret fields.
- Every pipeline run gets a unique `run_id` (UUID4) threaded through all log lines, and each LLM call logs prompt/completion/total token usage — for traceability without recording prompt or report content.
- Logs capture workflow events and high-level metadata, not full prompts, tool outputs, report content, or raw API responses.
- LLM calls retry with exponential back-off on rate-limit and timeout errors.
- All agent tool loops are capped at a maximum iteration count to prevent runaway calls.
- The research→marketing/sales pipeline uses conditional LangGraph edges, not just straight-through steps: an incomplete research profile aborts the run, and an incomplete sales email loops back to marketing for a fresh attempt, bounded by `_MAX_FEEDBACK_ATTEMPTS` so a persistently bad generation can't loop forever.
