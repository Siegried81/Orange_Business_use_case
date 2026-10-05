# Technical deep dive

Everything the [README](../README.md) leaves out: how a signal becomes a scored
Opportunity Space, what every score formula actually measures, which parts are an
LLM judgment and which are arithmetic, and the defects that shaped the code.

Figures come from `radar.db` — signals collected up to 30 August 2026, scored on
30 September 2026 — and from a real run of the test suite on 5 October 2026, not
from memory. `radar.db` is not versioned, so they describe that database, not
whatever a fresh clone would produce. Where a figure could only be obtained by
calling the LLM again it is marked as measured on an earlier run and not
re-verified. The dated reasoning behind each change lives in
[`decisions.md`](decisions.md).

---

## 1. The shape of the system

One pipeline, one SQLite file, two dashboards reading it.

```
   config.py  (17 verticals, closed taxonomy: 24 use cases x 15 technologies,
               Orange Business asset catalog, customer references, thresholds)
        │
        ▼
   ingest.py ── 9 sources, each call wrapped in safe_run()        ──► signals
        │        Google News, NewsAPI.ai, GDELT, vendor blogs,
        │        Hacker News, arXiv, Semantic Scholar, EUR-Lex, TED
        ▼
   analyze.py ── ONE LLM call per vertical: titles -> candidate
        │        (vertical, use_case, technology) triples
        │        in-taxonomy ──► recurring_themes    out-of-taxonomy ──► watchlist_terms
        ▼                                                      │
   radar_cli.py create / promote ──► opportunity_spaces        extend_taxonomy.py
        │        (27 seeds + recurring themes;                 ──► proposals
        │         UNIQUE(vertical, use_case, technology))       ──► radar_cli.py review (human)
        ▼                                                       ──► taxonomy_extensions.json
   radar_cli.py link ── keyword overlap, per vertical,
        │               top 56 signals per OS               ──► opportunity_signals
        ▼
   scoring.py ── 3 deterministic sub-scores + urgency (no LLM)
        │        2 LLM sub-scores, right-to-win, enrichment ──► scores, right_to_win_scores
        ▼
   radar_cli.py summary ──► opportunity_spaces_summary.md   (client-facing)
   app/streamlit_app.py  ──► interactive radar               (reads radar.db)
   scripts/export_powerbi.py ──► app/powerbi_data/*.csv ──► app/innovation_radar_dashboard.pbip
                                 (Power BI has no SQLite connector, so it reads a CSV export)
```

The order matters: **link runs before score**. Each OS is scored on the signals
linked to it, not on everything collected for its vertical. Getting that order
wrong was one of the first real bugs (§6.1).

State of the current database:

| | Count |
|---|---|
| Signals | 4,272 (published May 1992 to August 2026, mostly recent; 9 undated; last collected 30/08/2026) |
| Opportunity Spaces | 133 (OS001 to OS135; OS052 and OS053 were the removed duplicates) |
| Signal links | 3,319, every OS has at least one |
| Recurring themes tracked / promoted | 263 / 124 |
| Watchlist terms / proposals | 235 / 8 (all rejected) |
| Right-to-win levels | L0 10, L1 79, L2 0, L3 43, L4 1 |
| Quadrants (strong / needs capability / moderate / low) | 16 / 5 / 77 / 35 |

---

## 2. Understanding the scores — formulas and how to read them

Computed in `pipeline/scoring.py` on the signals linked to one OS. `n` = number of
linked signals.

### 2.1 Attractiveness (0–10) — five sub-scores, one weighted sum

| Sub-score | Weight | Formula | How to read it |
|---|---|---|---|
| Market signal strength | 30% | `min(10, n / 56 × 10)` | Raw volume. 56 = `MARKET_SIGNAL_CAP`, equal to `link`'s `top_n`, so an OS reaches 10 exactly when `link` has to start cutting. The current 90th percentile of keyword-matching signals per OS is 52 (`calibrate_output.txt`), so 56 is not that percentile. |
| Source diversity | 20% | `min(10, distinct source_name / 40 × 10)` | Counts distinct *publishers*, not connectors: for Google News and GDELT, `source_name` is the outlet (1,335 names in the DB for 9 connectors). TED and EUR-Lex are forced to one name each so a mirror site cannot count twice: 623 EUR-Lex signals and 185 TED signals sit under one name apiece. The fix only applies going forward, so 6 pre-fix rows still carry `docs.ted.europa.eu`; none of them is linked to an OS, so none reaches this sub-score. |
| Evidence quality | 25% | LLM, 0–10, on the first 15 linked titles with their source | "Are these specific, credible, relevant?" 0.0 when the OS has no signal; 5.0 with the justification "LLM scoring unavailable" when no provider answered. |
| Novelty / momentum | 10% | share of dated signals in the most recent third of the window [oldest signal, now], × 10 | Acceleration, not just recency. Uses `published_date`, falls back to `collected_at`. Neutral 5.0 below 3 datable signals. |
| Strategic relevance | 15% | LLM, 0–10, against the Orange Business asset catalog; +0.5 for Defense, Aerospace & Defense, Healthcare | The LLM sees the triple and the *number* of signals, not their titles. The bonus reflects Orange running a dedicated division for those verticals. |

```
total_score = 0.30·market + 0.20·diversity + 0.25·evidence + 0.10·novelty + 0.15·relevance
```

60% of the weight is deterministic, 40% is an LLM judgment. The weights are a
manual choice, not fitted to outcomes (see §8).

### 2.2 Right-to-win (0–10) — a separate axis, never summed with attractiveness

One LLM call classifies the OS on portfolio distance, given the 17-asset catalog,
analyst recognition, capability facts and the named customers of *that* vertical:

| Level | Meaning | Typical score |
|---|---|---|
| L0 | Direct offer: an existing asset does it as-is | 9–10 |
| L1 | Bundle: two or more assets, not yet packaged | |
| L2 | Partner-dependent | |
| L3 | Adjacent: one capability to build or acquire | |
| L4 | White space | 0–2 |

Then deterministic calibration: **+0.5 per named customer reference** in the vertical
(max +1.0), capped at 10 — the only bonus there is. A second bonus (+0.3 for CRM
opportunities in the vertical, +0.3 for pipeline value at or above the median) used
to be coded, but it read `OPPORTUNITY_COUNT_BY_VERTICAL` and
`PIPELINE_VALUE_BY_VERTICAL`, both empty, so it was always 0 while the
justification still advertised it; the function and its two config dicts are gone.
The right-to-win call does not read the linked signals at all — it judges the
triple against the portfolio.

The level is validated against `L0`–`L4` before it is stored: an invented level
falls back to `L4` and the justification says which value was rejected. The 0–10
score is parsed defensively too — a word where a number was asked for ("high")
gives the neutral 5.0, marked `unavailable`, instead of raising and killing the
run. On a provider failure it produces `L4 / 0.0` with "do not trust, re-run
scoring", which the database then refuses to write over a real score (§6.11).

### 2.3 Urgency (0–10) — shown apart, never in either axis

```
weight  = Σ regulation signals × 1.0
        + Σ buying_signal × max(0, 1 − age_days / 730)      (undated: 0.5)
        + 2.0 × novelty / 10                                 (only if n ≥ 3)
urgency = min(10, weight / scaling_point × 10)
```

`scaling_point` is the 95th percentile of `weight` across every scored OS,
recomputed on each run (`statistics.quantiles(..., method="inclusive")`, clamped to
the observed maximum, floor 1.0, fallback 6.0 when fewer than two OS have any
weight). Recomputed on 5 October 2026 against the committed database it is 3.68;
it was 3.69 when the stored urgency scores were written on 30 September. Nothing
in the database changed: buying signals lose weight as they age, so the scaling
point drifts with the calendar even on a frozen snapshot. **An OS's urgency can
move without anything about that OS changing** — that is the population moving
(or simply ageing), by design.

Only `regulation` and `buying_signal` count because they carry an external deadline
(a compliance date, a tender closing). 730 days is `TED_LOOKBACK_DAYS`, the same
constant that bounds TED ingestion.

### 2.4 Quadrants

`pipeline/config.py::quadrant()`, threshold `STRONG_THRESHOLD = 7` on both axes,
inclusive, shared by the overview counts and the detail panel:

| | Right-to-win ≥ 7 | Right-to-win < 7 |
|---|---|---|
| **Attractiveness ≥ 7** | strong | needs capability |
| **Attractiveness < 7** | moderate market | low on both |

A missing score yields no quadrant rather than "low".

---

## 3. Module reference

| Module | Lines | What it owns |
|---|---|---|
| `pipeline/config.py` | 580 | Verticals and their search seeds, the closed taxonomy (+ `taxonomy_extensions.json`), all source queries and rate-limit settings, the Orange Business catalog (17 assets, 18 customer references, 5 analyst facts, 12 capability stats), roles, personas, geographies, `STRONG_THRESHOLD`, `quadrant()`. |
| `pipeline/ingest.py` | 611 | One fetcher per source, `safe_run()` isolation, GDELT and Semantic Scholar disk-backed cooldowns (`logs/.source_cooldowns.json`), timestamped run log. |
| `pipeline/analyze.py` | 201 | Theme extraction: `_call_theme_extraction_llm()` (the only LLM boundary), `_classify_themes()` (pure, unit-tested), thin orchestrator; `--from=` resumes after a quota stop. |
| `pipeline/theme_promotion.py` | 46 | Tracks valid themes across runs; feeds `promote`. |
| `pipeline/extend_taxonomy.py` | 204 | Watchlist term → proposal once frequency ≥ 5. |
| `pipeline/taxonomy_validation.py` | 6 | Rejects bare generic terms ("AI") while keeping compounds ("Generative AI"). |
| `pipeline/scoring.py` | 837 | Every formula in §2, the four LLM calls, `--force`, `--from/--to`, `--refresh`, `--recalibrate-*`, `--rescue-fallback`, `--prune-scores`, automatic deterministic refresh of scores older than 3 days. |
| `pipeline/db.py` | 564 | Schema, all queries, duplicate-triple cleanup + `UNIQUE` index, latest-score queries with an `id DESC` tiebreaker. |
| `llm/llm_client.py` | 271 | Provider chain, Groq key rotation, JSON extraction. |
| `radar_cli.py` | 643 | `create`, `promote`, `link`, `summary [--top N] [--output PATH]`, `review`, `watchlist`, `themes`, `calibrate`, `dedupe`, `delete`, `scores`, `all`. |
| `app/streamlit_app.py` | 691 | Polar radar and bubble views, sidebar filters (vertical, domain, horizon, owning team, buyer persona, geography), per-OS breakdown and next actions per role. |
| `radar_cli_top_15.py`, `pipeline/signals_discovery.py` | 518, 58 | Older CLI and its copy of `theme_promotion`, kept on purpose (see `decisions.md`). |
| `scripts/` | | One-off healthcare analysis, run from the repo root. |
| `scripts/export_powerbi.py` | 224 | Exports `radar.db` (opened read-only) to the six CSV files the Power BI project reads, reports unparseable dates, and warns when the model's `DataFolder` points elsewhere; run it before a Refresh. |

### Data model

Eight tables. `signals` is deduplicated on `(source_url, title)`.
`opportunity_spaces` carries the triple, the enrichment fields and a unique label;
`opportunity_signals` is the many-to-many link. `scores` and `right_to_win_scores`
hold **one current row per OS**: `insert_score()` updates in place, so there is no
score history (decided 29/09, `decisions.md`). `watchlist_terms`, `recurring_themes`
and `proposals` drive taxonomy growth.

---

## 4. Where the LLM is used, and how it is contained

Five kinds of call, each a **single structured call with a fixed prompt** that must
return one JSON object: theme extraction (`analyze.py`), evidence quality,
strategic relevance, right-to-win, and enrichment (role, buyer persona, 1–3
geographies, horizon, domain, one next action per role). No agent, no tool use, no
multi-step loop.

- **Provider chain** (`LLM_PROVIDER=auto`): Groq (`openai/gpt-oss-120b`, up to 5
  keys, rotated only on 429/quota errors) → Cerebras → SambaNova → local Ollama
  (`llama3.2:3b`). A provider with no key is skipped. Temperature 0 on Groq, Cerebras and SambaNova;
  the Ollama call sends no temperature, so it runs at the model's default.
- **Closed vocabularies**: the LLM must pick use cases and technologies from the
  taxonomy, domains from `DOMAINS_TAXONOMY`; anything else goes to the watchlist
  (themes) or is set to `None` (domain).
- **Grounding**: strategic relevance and right-to-win prompts embed the real asset
  catalog and cited customer references, and ask the model to name the asset.
- **Fallbacks, never crashes**: a missing *required* key (`score`, `portfolio_distance`,
  `role`) makes the whole result fall back (5.0 neutral, L4/0, "review manually").
  Other keys get a silent default (`right_to_win_score` 0, `horizon` "Later"), and
  `portfolio_distance` is not checked against L0–L4. `tests/test_LLM.py` covers
  `None`, empty dicts and missing keys; hallucinated values are tested only for the
  enrichment domain and geography.

Everything countable — volume, diversity, momentum, urgency, linking — is kept out
of the LLM on purpose.

---

## 5. Testing

128 tests, all passing on 5 October 2026, no network (`python -m pytest`, a few
seconds). "No network" is now enforced rather than reviewed: an autouse fixture in
`conftest.py` makes `socket.connect` / `create_connection` raise, so a test that
forgets to mock its feed or LLM call fails with an explicit message instead of
reaching the internet.

| File | Tests | Covers |
|---|---|---|
| `tests/test_LLM.py` | 43 | Malformed LLM output across `analyze`, `scoring`, `extend_taxonomy`; generic-term filter; key rotation; non-numeric scores ("high") coerced to a marked neutral instead of raising; `portfolio_distance` validated against L0–L4; the network block itself. |
| `tests/test_export_powerbi.py` | 17 | Power BI export: date formats to UTC, missing stays empty (never 0), unparseable dates reported, stable header with no rows, HTML summaries to text, `DataFolder` mismatch warning, read-only DB. |
| `tests/test_scoring_and_db.py` | 54 | Novelty, urgency scaling and caps, latest-score queries, deletes, deterministic refresh (LLM fields untouched, row updated in place), watchlist guard, quadrants, summary header under `--top`, rounded sub-scores, urgency wording in the summary, one source name for TED, removal of old non-tech links, and the refusal to overwrite a real score with a fallback. |
| `tests/test_dashboard.py` | 14 | Dashboard, through Streamlit's `AppTest`: missing `radar.db` reported as an error with the pipeline hint, the AI-generated disclosure rendered on the page, each tab opened exactly once, an all-NaN top-opportunity selection, and which `source_url` values become links. |

Network calls are mocked (`feedparser.parse` is monkeypatched; LLM calls are
monkeypatched at `scoring.get_llm_json`). There is no CI; the suite is run by hand.

---

## 6. Bugs encountered and fixed

Almost none of these crashed. The typical failure was a number that computed
cleanly and meant something else — several of them in the client-facing summary.

### 6.1 Scoring ran before linking
Every OS was scored on all the signals of its vertical, so OS in the same vertical
were scored on the same evidence. Fixed by making
`link` a pipeline step before `score`, and scoring on `opportunity_signals` only.

### 6.2 The same opportunity registered twice
`create()`'s duplicate check only printed a warning, so OS026/OS052 and OS036/OS053
were the same triple under two labels — and appeared twice in the client summary,
with the same 43 signals counted twice (a count taken before the duplicates were
deleted; the rows are gone, so it cannot be re-derived from the current
database). Fixed with a blocking check plus a
database `UNIQUE(vertical, use_case, technology)` index, so no code path can do it
again.

### 6.3 A "95th percentile" above the maximum
The default percentile method extrapolates on small samples: a 4-value test batch
returned 15.25 when the highest value was 10. Fixed with `method="inclusive"` and
a clamp to the observed maximum.

### 6.4 The whole test suite collected zero tests
`llm/` was missing from the repository, so every import of `get_llm_json` failed.
Restored from the project backup.

### 6.5 Two thresholds for "strong"
The overview used 8, the detail panel 7, and a right-to-win in [7, 8) fell in no
quadrant at all. One constant now: overview counts went from 5/1/66/61 to
12/4/87/30 on the data of 29 September (that scoring run has been overwritten, so
those two figures are not re-derivable). On the committed database the same
comparison now reads 11/2/61/59 at a threshold of 8 against the 16/5/77/35 of §1
at 7 — the shape of the point, not the exact counts, is what carried.

### 6.6 Two Groq keys never used
`.env` defined `GROQ_API_KEY_4` and `_5`; the client read keys 1–3, so runs fell
back to slower providers with quota still available.

### 6.7 The client summary said 118 of 133 OS were unscored
With `--top 15`, every OS left out by the cut was counted as "not yet scored". All
133 were scored. The header now reads "133/133 scored, top 15 shown". The same
file printed sub-scores like `7.857142857142857`; they are now rounded for display.

### 6.8 One source counted as two
TED notices from the API and from the Google News fallback had different
`source_name`s, and 251 EUR-Lex documents were attributed to the domain that
mirrored them (`consultaelectoral.onpe.gob.pe`) — a count taken before the fix;
no row carries that domain any more, so it cannot be re-derived. Each inflated
`source_diversity`. Both sources now have one fixed name in `ingest.py`, and the
database holds 623 EUR-Lex signals under the single name.

### 6.9 Eight OS scored with no evidence
OS128–OS135 had been promoted after the last `link`, then scored on zero signals:
attractiveness around 1, right-to-win up to 9.5. Re-linked, then rescored.

### 6.10 A filter that only worked on new data
`NON_TECH_SOURCES` excluded sports and local outlets at link time, but `link` only
adds rows, so older links to those outlets stayed. `link` now removes them first.

### 6.11 A rescore that silently wrote fallbacks over real scores
The first `--force` on OS128–OS135 ran in an environment without the `groq`
package; every provider failed, and the run **exited 0** after writing L4/0 and
neutral 5.0 over the previous values. Caught by reading the log, fixed at the
time by installing `requirements.txt` and re-running.

The underlying behaviour is now fixed too. Every degraded justification carries
the substring `unavailable` (`scoring.FALLBACK_MARKER`), and
`db.insert_score()` / `db.insert_right_to_win_score()` refuse a write that would
replace a non-fallback justification with a fallback one: the previous real score
stays, the refusal is printed per opportunity space, and
`scoring.score_all_opportunity_spaces()` returns the refused labels so
`python -m pipeline.scoring` exits non-zero. A fallback can still land on an
empty row or on top of another fallback, which is what `--rescue-fallback`
needs.

---

## 7. The lessons, extracted

1. **The dangerous bug is the one that runs.** 6.1, 6.2, 6.5, 6.7 and 6.8 all
   produced plausible numbers.
2. **A filter or a fix that only applies going forward leaves the old data wrong.**
   6.10, and 6.9 — new OS were never re-linked.
3. **The name of a thing is part of the measurement.** Two names for one source
   is two sources.
4. **A degraded result must not look like a result.** 6.11 — the database now
   refuses the write and the run exits non-zero.
5. **Constraints belong in the database, not in a warning.** 6.2.
6. **One constant per concept.** 6.5.

---

## 8. Reproducibility, honestly, and open issues

The code, the client summary **and the data** are in git. `radar.db` (6.8 MB) is
committed on purpose: Streamlit Cloud builds from the repository and has nothing
to run the pipeline with, so the deployed dashboard would be empty without it. It
also makes the numbers in this document reproducible, which a fresh ingest would
not be — the news has moved on. The flip side is that the committed database is a
snapshot: it only changes when someone re-runs the pipeline and commits the
result.

A full run needs `pip install -r requirements.txt` (pinned to the versions the
project is tested against; `streamlit` is a `>=1.49` floor, since Streamlit Cloud
upgrades its own runtime) and at least one working LLM key. The model is called at
temperature 0, which makes repeated calls close, not identical.

| Open issue | Why it matters |
|---|---|
| `link` accepts a single shared keyword | 12 links (6 articles from general outlets) are off-topic: "Natasha Cloud" → Cloud, strikes on Ukraine's "energy sector" → Energy. That 12/6 is a hand count over the link table, not a query, so it is not re-derived here; the two named articles account for 4 of the links in the committed database. Requiring 2 keywords would change every link — team decision. |
| Fallback scores are kept until someone re-runs | A refused write (§6.11) leaves yesterday's real score in place and exits non-zero, which is the right default but means the radar can hold a score older than its signals. `--rescue-fallback` re-scores the spaces still on a fallback; none of the 133 is on one in the committed database. |
| Right-to-win never reads the signals | By design it judges the triple against the portfolio, but it means evidence cannot move it. |
| Strategic relevance sees only the signal count | Not the titles; two OS with the same triple wording and count get the same prompt. |
| Weights are manual | 30/20/25/10/15 were chosen by hand. `tests/ahp_weights.xlsx` is an AHP pairwise-comparison sheet, not wired into the code. |
| Taxonomy thresholds disagree | `generate_all_proposals()` defaults to 3 but is called with 5; `RECURRING_THEME_PROMOTION_THRESHOLD` is 2 and is the only promotion threshold. None calibrated. |
| Right-to-win calibration is one hand-set bonus | +0.5 per customer reference in the vertical, capped at +1.0, chosen by hand and not fitted to anything. The older pipeline-value bonus is gone along with its two empty config dicts (§2.2). |
| Cerebras and SambaNova keys fail | 402 and 401 on 29/09: the fallback chain is effectively Groq → Ollama. Not re-tested here — checking it means calling the providers. |
| `source_diversity` counts publishers | With a cap of 40, an OS linked to many small outlets scores high on diversity. 7 of the 133 OS are at or above the cap; the widest has 45 distinct publishers, the median 12. |
| Power BI refresh path | Power Query has no relative file paths, so the `DataFolder` parameter is an absolute path to one machine; `export_powerbi.py` now warns and prints the right value when it does not match. The CSV export must be re-run by hand after each pipeline run. The quadrant threshold 7 is restated in the model instead of read from `STRONG_THRESHOLD`. |
| Right-to-win is not stable across runs | Two scoring runs on the same linked signals (29/09 and 30/09) moved 38 of 133 OS to another level, 30 of them between L1 and L3. The top 15 by attractiveness kept the same members, in a different order. **Measured on those two runs and not re-verified**: `insert_score()` updates in place, so the database keeps no score history to compare, and a fresh comparison means a second LLM run. |
| Dependencies pinned, except Streamlit | `requirements.txt` pins exact versions for the other eight packages; `streamlit>=1.49` is a floor because Streamlit Cloud upgrades its own runtime. A pin is the version the project was tested against, not a version anyone validated as the best. |
