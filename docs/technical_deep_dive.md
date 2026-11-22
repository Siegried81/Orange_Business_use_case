# Technical deep dive

How a signal becomes a scored Opportunity Space, what each score measures, and which parts are LLM judgement and which are arithmetic. Design rationale is in [decisions.md](decisions.md); setup is in the [README](../README.md).

## 1. Architecture

One pipeline, one SQLite file (`radar.db`), two front ends reading it.

```
   config.py  (verticals, closed taxonomy: use cases x technologies,
               Orange Business asset catalog, customer references, thresholds)
        |
        v
   ingest.py ---- 9 sources, each call wrapped in safe_run()          --> signals
        |         Google News, NewsAPI.ai, GDELT, vendor blogs,
        |         Hacker News, arXiv, Semantic Scholar, EUR-Lex, TED
        v
   analyze.py --- one LLM call per vertical: titles -> candidate
        |         (vertical, use_case, technology) triples
        |         in taxonomy --> recurring_themes   out of it --> watchlist_terms
        v                                                  |
   radar_cli.py create / promote --> opportunity_spaces    extend_taxonomy.py --> proposals
        |         (seeds + recurring themes;                   --> radar_cli.py review (human)
        |          UNIQUE(vertical, use_case, technology))     --> taxonomy_extensions.json
        v
   radar_cli.py link --- >= 2 shared keywords, per vertical,
        |                top_n signals per space            --> opportunity_signals
        v
   scoring.py --- deterministic sub-scores + urgency (no LLM)
        |         LLM sub-scores, right-to-win, enrichment  --> scores, right_to_win_scores
        v
   radar_cli.py summary         --> opportunity_spaces_summary.md (generated, not committed)
   app/streamlit_app.py         --> interactive dashboard (reads radar.db)
   scripts/export_powerbi.py    --> app/powerbi_data/*.csv --> app/innovation_radar_dashboard.pbip
```

Link runs before score: each space is scored on the signals linked to it, not on everything collected for its vertical. `radar_cli.py all` runs the whole chain in order; `dags/innovation_radar.py` runs the same stages under Airflow.

## 2. Scores

Computed in `pipeline/scoring.py` on the signals linked to one space; `n` is the number of linked signals.

### 2.1 Attractiveness (0-10)

Five sub-scores, one weighted sum:

| Sub-score | Formula | Notes |
|---|---|---|
| Market signal strength | `min(10, n / MARKET_SIGNAL_CAP x 10)` | Raw volume. |
| Source diversity | `min(10, distinct source_name / SOURCE_DIVERSITY_CAP x 10)` | Counts distinct publishers, not connectors. TED and EUR-Lex are forced to one name each so a mirror site cannot count twice. |
| Evidence quality | LLM, 0-10, on the first 15 linked titles and their source | 0.0 with no signal; neutral 5.0 ("LLM scoring unavailable") when no provider answered. |
| Novelty / momentum | share of dated signals in the most recent third of the window [oldest signal, now], x 10 | Acceleration, not recency. Uses `published_date`, falls back to `collected_at`. Neutral 5.0 below 3 datable signals. |
| Strategic relevance | LLM, 0-10, against the Orange Business asset catalog | The LLM sees the triple and the number of signals, not their titles. +0.5 for Defense, Aerospace & Defense and Healthcare, where Orange runs a dedicated division. |

```
total_score = sum(weight_k x sub_score_k)      (weights sum to 1)
```

Default weights are the AHP priority vector of `pipeline/ahp_weights.xlsx` (`pipeline/ahp.py`: normalised row geometric means; judgements with a consistency ratio of 0.10 or more are refused). The hand-set weights remain selectable with `python -m pipeline.scoring --weights=manual` or `WEIGHTING` in `scoring.py`. Market signal strength, source diversity and novelty are deterministic; evidence quality and strategic relevance are LLM judgements.

The two caps are the linked-signal and publisher counts at which a sub-score reaches 10. They are derived by `scripts/derive_caps.py`: each cap sits at the percentile position its predecessor held in the per-space distribution (mid-rank percentile, inclusive quantile, rounded), replaying the link rule through `radar_cli.matching_signals()` so the result does not depend on stored links. `tests/test_derive_caps.py` fails when the constants drift from the derivation.

### 2.2 Right-to-win (0-10)

A separate axis, never summed with attractiveness. One LLM call classifies the space on portfolio distance, given the asset catalog, analyst recognition, capability facts and the named customers of that vertical. It does not read the linked signals.

| Level | Meaning |
|---|---|
| L0 | Direct offer: an existing asset does it as is |
| L1 | Bundle: two or more assets, not yet packaged |
| L2 | Partner-dependent |
| L3 | Adjacent: one capability to build or acquire |
| L4 | White space |

Then a deterministic calibration: `CUSTOMER_REFERENCE_BONUS` (0.5) per named customer reference in the vertical, capped at `CUSTOMER_REFERENCE_BONUS_CAP` (1.0), total capped at 10. Both values are hand-set (no outcome data to fit them); `scripts/rtw_bonus_sensitivity.py` re-applies other values to the stored scores, with no LLM call, to show what moves. The level is validated against L0-L4 (an invented level becomes L4 and the justification says so); a word, `nan` or `inf` where a number was asked for gives a marked neutral score.

### 2.3 Urgency (0-10)

Shown apart, never part of either axis.

```
weight  = sum(regulation signals x 1.0)
        + sum(buying_signal x max(0, 1 - age_days / 730))     (undated: 0.5)
        + 2.0 x novelty / 10                                   (only if n >= 3)
urgency = min(10, weight / scaling_point x 10)
```

`scaling_point` is the 95th percentile of `weight` across all scored spaces (`statistics.quantiles(..., method="inclusive")`, clamped to the observed maximum, floor 1.0, fallback 6.0 with fewer than two weighted spaces), recomputed on every run, including `--rescue-fallback`. A space's urgency can therefore move without anything about it changing: the population moves, and buying signals lose weight as they age. Only `regulation` and `buying_signal` count because they carry an external deadline; 730 days is `TED_LOOKBACK_DAYS`.

### 2.4 Quadrants

`pipeline/config.py::quadrant()`, with `STRONG_THRESHOLD = 7` on both axes (inclusive), shared by the overview and the detail panel:

| | Right-to-win >= 7 | Right-to-win < 7 |
|---|---|---|
| **Attractiveness >= 7** | strong | needs capability |
| **Attractiveness < 7** | moderate market | low on both |

A missing score yields no quadrant. A space with no linked signal is put in a fifth category, insufficient evidence, before the grid is read; the Power BI model applies the same rule from the `linked_signals` column of `latest_scores.csv`.

## 3. Modules

| Module | Owns |
|---|---|
| `pipeline/config.py` | Verticals and search seeds, the closed taxonomy (+ `taxonomy_extensions.json`), source queries and rate-limit settings, the Orange Business catalog (assets, customer references, analyst facts, capability stats), roles, personas, `STRONG_THRESHOLD`, `quadrant()`, `TAXONOMY_PROPOSAL_THRESHOLD`. |
| `pipeline/ingest.py` | One fetcher per source, `safe_run()` isolation, feeds downloaded with a timeout and an error on HTTP failure, disk-backed GDELT and Semantic Scholar cooldowns (`logs/.source_cooldowns.json`), timestamped run log. |
| `pipeline/analyze.py` | Theme extraction: `_call_theme_extraction_llm()` (the only LLM boundary), `_classify_themes()` (pure, unit-tested); `--from=` resumes after a quota stop. |
| `pipeline/theme_promotion.py` | Tracks valid themes across runs; feeds `promote`. |
| `pipeline/extend_taxonomy.py` | Watchlist term to proposal once its frequency reaches `TAXONOMY_PROPOSAL_THRESHOLD`. |
| `pipeline/taxonomy_validation.py` | Rejects bare generic terms ("AI") while keeping compounds ("Generative AI"). |
| `pipeline/ahp.py` | AHP priority vector and consistency ratio from `ahp_weights.xlsx`. |
| `pipeline/scoring.py` | Every formula in section 2, the four LLM calls, and the flags `--force`, `--from/--to`, `--refresh`, `--weights`, `--recalibrate-*`, `--rescue-fallback`, `--prune-scores`; scores older than 3 days get their deterministic part refreshed automatically. |
| `pipeline/db.py` | Schema, all queries, duplicate-triple cleanup and `UNIQUE` index, latest-score queries with an `id DESC` tiebreaker, foreign-key repair, append-only score history. |
| `llm/llm_client.py` | Provider chain, Groq key rotation, JSON extraction. |
| `radar_cli.py` | `create`, `promote`, `link`, `summary [--top N] [--output PATH]`, `review`, `watchlist`, `themes`, `calibrate`, `dedupe`, `delete`, `scores`, `all`. |
| `app/streamlit_app.py` | Radar (cartesian axes, so clicks register) and bubble views, sidebar filters, per-space breakdown and next actions per role, data dates, pipeline-growth section. |
| `scripts/` | `derive_caps.py` and `rtw_bonus_sensitivity.py` (read-only), `export_powerbi.py` (opens `radar.db` read-only, writes the six CSV files, warns when the model's `DataFolder` points elsewhere). |

### Data model

`signals` is deduplicated on `(source_url, title)`. `opportunity_spaces` carries the triple, the enrichment fields and a unique label; `opportunity_signals` is the many-to-many link. `scores` and `right_to_win_scores` hold one current row per space (`insert_score()` updates in place) and every read uses them. `score_history` and `right_to_win_history` (`HISTORY_SCHEMA`) get a copy of each accepted write, with the label and triple stored because `wipe` resets the id sequence; they have no foreign key. `watchlist_terms`, `recurring_themes` and `proposals` drive taxonomy growth.

## 4. Where the LLM is used

Five kinds of call, each a single structured call with a fixed prompt returning one JSON object: theme extraction, evidence quality, strategic relevance, right-to-win, and enrichment (role, buyer persona, geographies, horizon, domain, one next action per role). No agent, no tool use, no loop. Everything countable (volume, diversity, momentum, urgency, linking) stays out of the LLM on purpose.

- **Provider chain** (`LLM_PROVIDER=auto`): Groq (up to 5 keys, rotated on 429/quota errors), then SambaNova, then local Ollama. A provider with no key is skipped. Temperature 0 on Groq and SambaNova; the Ollama call sends none.
- **Closed vocabularies**: the model must pick use cases and technologies from the taxonomy and domains from `DOMAINS_TAXONOMY`; anything else goes to the watchlist (themes) or becomes `None` (domain).
- **Grounding**: strategic relevance and right-to-win prompts embed the real asset catalog and customer references.
- **Fallbacks, never crashes**: a missing required key (`score`, `portfolio_distance`, `role`) makes the whole result fall back (neutral 5.0, L4/0, "review manually"); finite scores outside 0-10 are clamped. A fallback never overwrites a real score or real enrichment, and a scoring run with no answering provider exits non-zero. `--rescue-fallback` re-scores the spaces still on a fallback.
- **Untrusted titles**: the evidence-quality prompt fences titles in a `<signals>` block declared to be data, and defangs any spelling of that tag inside a title.

Scores come from a model at temperature 0, so repeated runs are close, not identical; right-to-win in particular can move a space between levels from one run to the next.

## 5. Reproducing a run

```bash
pip install -r requirements.txt && cp .env.example .env   # at least one LLM key
python radar_cli.py all          # or the stages one by one: pipeline.ingest, pipeline.analyze, radar_cli.py create/promote/link, pipeline.scoring, radar_cli.py summary
python scripts/export_powerbi.py # optional: CSV export for Power BI
```

`python -m pytest -q` runs the offline suite. The committed `radar.db` is a snapshot: a fresh ingest returns different news, so figures from a new run will not match it.
