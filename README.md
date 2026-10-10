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
│   ├── theme_promotion.py       # tracks valid themes across runs, feeds promote
│   ├── latest_scores.py         # prints the most recent score per opportunity space
│   ├── signals_discovery.py     # older copy of theme tracking, used by radar_cli_top_15.py
│   └── db.py                    # SQLite access layer (radar.db)
├── llm/
│   └── llm_client.py            # provider-agnostic LLM client (Groq -> Cerebras -> SambaNova -> Ollama)
├── app/
│   ├── streamlit_app.py         # interactive dashboard
│   ├── innovation_radar_dashboard.pbip  # Power BI project (open this one)
│   ├── innovation_radar_dashboard.Report/        # report pages and visuals (part of the .pbip)
│   ├── innovation_radar_dashboard.SemanticModel/ # tables, relationships, DAX measures (part of the .pbip)
│   ├── innovation_radar_dashboard.pbix  # previous Power BI file, kept as backup
│   └── powerbi_data/            # CSV export of radar.db read by Power BI (generated)
├── radar_cli.py                  # CLI entry point (create/promote/link/summary/all/...)
├── radar_cli_top_15.py           # older CLI, kept on purpose (see docs/decisions.md)
├── opportunity_spaces_summary.md         # client-facing summary, all opportunity spaces (generated)
├── opportunity_spaces_summary_top_15.md  # same, top 15 by attractiveness (generated)
├── screenshots/                  # dashboard screenshots
├── conftest.py                   # repo root on sys.path + autouse fixture blocking all sockets
├── tests/
│   ├── test_LLM.py               # malformed/missing LLM output handling, key rotation
│   ├── test_export_powerbi.py    # Power BI CSV export: dates, missing values, DataFolder check
│   ├── test_scoring_and_db.py    # scoring formulas, DB queries, dashboard quadrants
│   └── test_dashboard.py         # Streamlit dashboard: missing DB, AI disclosure, tabs, links
├── scripts/                      # one-off analysis scripts, run from the repo root
│   ├── export_powerbi.py         # radar.db -> app/powerbi_data/*.csv for Power BI
│   ├── analyze_healthcare.py     # vertical deep-dive report
│   └── check_healthcare.py       # vertical diagnostic query
├── docs/
│   ├── decisions.md              # dated decision log
│   └── technical_deep_dive.md    # score formulas, modules, bugs fixed, open issues
├── radar.db                      # SQLite database (6.8 MB, tracked on purpose: Streamlit Cloud deploys from the repo and cannot run the pipeline)
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
                                   # exits non-zero if no LLM provider answered: the previous real scores are kept, not overwritten
python -m pipeline.scoring --rescue-fallback   # re-score the spaces still sitting on a neutral fallback
python radar_cli.py summary        # write opportunity_spaces_summary.md
python radar_cli.py summary --top 15 --output opportunity_spaces_summary_top_15.md
python radar_cli.py review         # approve/reject taxonomy proposals
python -m pytest                   # run the 136 tests (sockets are blocked by conftest.py, so no network is possible)
```

Power BI dashboard (Power BI has no SQLite connector, so it reads a CSV export):

```bash
python scripts/export_powerbi.py   # radar.db -> app/powerbi_data/*.csv
```

Then open `app/innovation_radar_dashboard.pbip` and click Refresh. The CSV folder is the
`DataFolder` parameter (Transform data > Manage parameters). It holds an absolute path,
so set it on a fresh clone or if the repo moves; the export script prints a warning with
the value to use when it does not match.

## Deploying

- **Docker**: `docker compose up --build` serves the dashboard on
  http://localhost:8501 with the host's `radar.db` mounted in, and
  `docker compose run --rm dashboard python radar_cli.py all` runs the
  pipeline in the same image with the keys from `.env`.
- **Render**: `render.yaml` deploys the same image as a free web service
  serving the committed `radar.db`; push a new database to refresh it. Not
  exercised by CI: the first deploy is the test.
- **Streamlit Community Cloud**: point it at `app/streamlit_app.py`;
  `requirements.txt` and `.streamlit/config.toml` are read as they are, and
  the dashboard needs no secret.

CI (`.github/workflows/tests.yml`) runs the suite and builds the image on
every push.

## Key challenges

- **LLM reliability**: the local Ollama fallback (used when Groq, Cerebras and SambaNova all fail) can return malformed or incomplete JSON. I built explicit fallback branches so a missing field degrades to a documented neutral score instead of crashing or corrupting the database — covered by `tests/test_LLM.py`.
- **Calibration on a moving dataset**: the urgency scale is re-derived on every run from the 95th percentile of the live opportunity-space population rather than hardcoded, since the dataset kept growing (133 spaces today) as sources came online. `radar_cli.py calibrate` prints signal-count percentiles to help choose how many signals `link` keeps per space. Near-duplicate signal detection still uses a fixed 0.85 similarity.
- **Data correctness under concurrency**: SQLite timestamp collisions (two scores inserted milliseconds apart on the same machine) could silently pick the wrong "latest" score; the latest-score queries in `pipeline/db.py` use a deterministic tiebreaker (`id DESC`), and `insert_score()` updates each opportunity space's row in place, so there is one current score per space rather than a history.

## Tech stack

Python, pandas, SQLite, Groq/Cerebras/SambaNova/Ollama (LLM scoring), Streamlit, Plotly, Power BI (export).