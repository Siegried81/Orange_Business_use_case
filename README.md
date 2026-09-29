# Innovation Radar

A signal-driven opportunity discovery tool built for an Orange Business use case. It ingests external market signals (news, research, regulation, public procurement...), scores them into ranked Opportunity Spaces, and surfaces them through an interactive dashboard.

## What it does

I built a pipeline that turns scattered external signals into scored, ranked opportunity spaces:

- **Ingest**: pulls signals from 9 sources (Google News, NewsAPI.ai, GDELT, arXiv, Semantic Scholar, vendor blogs, Hacker News, EUR-Lex, TED), each isolated so one failing source never blocks the rest.
- **Link**: attaches signals to the opportunity space they actually support, before any scoring happens.
- **Score**: two independent axes — Attractiveness (market signal strength 30%, evidence quality 25%, source diversity 20%, strategic relevance 15%, novelty/momentum 10%) and Right-to-Win (portfolio distance L0–L4) — plus a separate Urgency score, combining LLM judgment (Groq, falling back to Cerebras, SambaNova, then a local Ollama model) and deterministic formulas. The dashboard calls an opportunity "strong" when both axes are >= 7 (`pipeline/config.py::STRONG_THRESHOLD`).
- **Promote**: recurring themes are promoted into new tracked Opportunity Spaces; a separate taxonomy-extension loop (watchlist → proposal → accepted/rejected) lets the underlying vocabulary grow over time.
- **Visualize**: a Streamlit dashboard (polar radar + bubble view, role-based next actions) and a Power BI export for reporting.

## Repository structure

```
.
├── pipeline/
│   ├── config.py               # taxonomy, thresholds, source lists
│   ├── ingest.py                # per-source connectors + safe_run() isolation
│   ├── analyze.py               # theme/OS extraction from raw signals
│   ├── scoring.py               # attractiveness + right-to-win scoring, LLM calls
│   ├── taxonomy_validation.py   # generic-term filtering
│   ├── extend_taxonomy.py       # watchlist -> proposal workflow
│   └── db.py                    # SQLite access layer (radar.db)
├── llm/
│   └── llm_client.py            # provider-agnostic LLM client (Groq -> Cerebras -> SambaNova -> Ollama)
├── app/
│   ├── streamlit_app.py         # interactive dashboard
│   └── innovation_radar_dashboard.pbix  # Power BI report
├── radar_cli.py                  # CLI entry point (create/promote/link/summary/all/...)
├── tests/
│   ├── test_LLM.py               # malformed/missing LLM output handling, key rotation
│   └── test_scoring_and_db.py    # scoring formulas, DB queries, dashboard quadrants
├── scripts/                      # one-off analysis scripts, run from the repo root
│   ├── analyze_healthcare.py     # vertical deep-dive report
│   └── check_healthcare.py       # vertical diagnostic query
├── docs/
│   ├── decisions.md              # dated decision log
│   └── technical_deep_dive.md    # score formulas, modules, bugs fixed, open issues
├── radar.db                      # SQLite database (generated, not tracked)
├── .env.example                   # required environment variables (no real keys)
├── requirements.txt
└── README.md
```

## Setup

```bash
git clone https://github.com/Siegried81/Orange_Business_use_case
cd Orange_Business_use_case
pip install -r requirements.txt
cp .env.example .env   # fill in your own API keys
```

## Usage

```bash
python radar_cli.py all            # db -> ingest -> analyze -> extend_taxonomy -> create -> promote -> link -> scoring -> summary
python -m streamlit run app/streamlit_app.py
```

Individual steps:

```bash
python -m pipeline.ingest          # collect signals
python -m pipeline.analyze         # extract candidate themes (LLM)
python radar_cli.py create         # register seed opportunity spaces
python radar_cli.py promote        # register recurring themes
python radar_cli.py link           # attach signals to each opportunity space
python -m pipeline.scoring         # score (LLM); --refresh recomputes the deterministic sub-scores only
python radar_cli.py summary        # write opportunity_spaces_summary.md
python radar_cli.py review         # approve/reject taxonomy proposals
python -m pytest                   # run the tests (no network needed)
```

## Key challenges

- **LLM reliability**: the Ollama fallback (used when Groq hits quota) can return malformed or incomplete JSON. I built explicit fallback branches so a missing field degrades to a documented neutral score instead of crashing or corrupting the database — covered by `tests/test_LLM.py`.
- **Calibration on a moving dataset**: thresholds (urgency scaling, duplicate detection) are re-derived from percentile statistics on the live opportunity-space population rather than hardcoded, since the dataset kept growing (39 → 100+ spaces) as sources came online.
- **Data correctness under concurrency**: SQLite timestamp collisions (two scores inserted milliseconds apart on the same machine) could silently pick the wrong "latest" score; the latest-score queries in `pipeline/db.py` use a deterministic tiebreaker (`id DESC`), and `insert_score()` updates each opportunity space's row in place, so there is one current score per space rather than a history.

## Tech stack

Python, pandas, SQLite, Groq/Cerebras/SambaNova/Ollama (LLM scoring), Streamlit, Plotly, Power BI (export).