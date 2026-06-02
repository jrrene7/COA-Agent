# Customer Outreach Automation Agent

A multi-agent pipeline that researches a sales lead, crafts personalised outreach, and writes a structured Markdown report to disk(for now :).

## Architecture

```
Lead Input (company name + URL)
         │
    Orchestrator
    ┌────┴────────────────────────┐
    │                             │
Research Agent → Marketing Agent → Sales Agent
                                       │
                               Report Writer
                                       │
                              output/<slug>_lead_report.md
```

| Agent | Responsibility | Output |
|---|---|---|
| **Research** | Scrapes website + web search for company intel | `research_data` |
| **Marketing** | ICP fit, value props, tone, competitors | `marketing_data` |
| **Sales** | Cold email, LinkedIn note, follow-ups, objections | `sales_data` |
| **ReportWriter** | Assembles Markdown report | `report_path` |

## Project Structure

```
COA-Agent/
├── agents/
│   ├── state.py           # OutreachState TypedDict
│   ├── research_agent.py  # ResearchAgent
│   ├── marketing_agent.py # MarketingAgent
│   ├── sales_agent.py     # SalesAgent
│   ├── report_writer.py   # ReportWriter
│   └── orchestrator.py    # Orchestrator (state machine)
├── lib/
│   ├── llm.py             # OpenAI wrapper with retry logic
│   ├── messages.py        # Message dataclasses
│   ├── tooling.py         # @tool decorator + Tool class
│   ├── state_machine.py   # StateMachine, Step, EntryPoint, Termination, Run
│   └── memory.py          # ShortTermMemory
├── tools/
│   ├── web_tools.py       # web_search, scrape_website
│   └── file_tools.py      # write_report
├── output/                # Generated .md reports land here (git-ignored)
├── config.env             # API keys — DO NOT COMMIT
├── requirements.txt
├── main.py                # Single-lead entry point
├── main_batch.py          # Batch entry point
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
- `write_report` sanitises filenames and prevents path traversal.
- LLM calls retry with exponential back-off on rate-limit and timeout errors.
- All agent tool loops are capped at a maximum iteration count to prevent runaway calls.
