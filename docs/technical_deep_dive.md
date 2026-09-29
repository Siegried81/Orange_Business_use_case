# Technical deep dive

Everything the [README](../README.md) leaves out: how a signal becomes a scored
Opportunity Space, what every score formula actually measures, which parts are an
LLM judgment and which are arithmetic, and the defects that shaped the code.

Figures come from `radar.db` and a real run of the test suite on 29 September 2026,
not from memory. `radar.db` is not versioned, so they describe that database, not
whatever a fresh clone would produce. The dated reasoning behind each change lives
in [`decisions.md`](decisions.md).

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
   app/innovation_radar_dashboard.pbix ──► Power BI report
```

The order matters: **link runs before score**. Each OS is scored on the signals
linked to it, not on everything collected for its vertical. Getting that order
wrong was one of the first real bugs (§6.1).

State of the current database:

| | Count |
|---|---|
| Signals | 4,272 (published 2012 to August 2026, last collected 30/08/2026) |
| Opportunity Spaces | 133 (OS001 to OS135; OS052 and OS053 were the removed duplicates) |
| Signal links | 3,319, every OS has at least one |
| Recurring themes tracked / promoted | 263 / 124 |
| Watchlist terms / proposals | 235 / 8 (all rejected) |
| Right-to-win levels | L0 12, L1 77, L2 1, L3 43, L4 0 |
| Quadrants (strong / needs capability / moderate / low) | 17 / 6 / 81 / 29 |

---

## 2. Understanding the scores — formulas and how to read them

Computed in `pipeline/scoring.py` on the signals linked to one OS. `n` = number of
linked signals.

### 2.1 Attractiveness (0–10) — five sub-scores, one weighted sum

| Sub-score | Weight | Formula | How to read it |
|---|---|---|---|
| Market signal strength | 30% | `min(10, n / 56 × 10)` | Raw volume. 56 = `MARKET_SIGNAL_CAP`, set to the 90th percentile of keyword-matching signals per OS and to `link`'s `top_n`, so an OS reaches 10 exactly when `link` has to start cutting. |
| Source diversity | 20% | `min(10, distinct source_name / 40 × 10)` | Counts distinct *publishers*, not connectors: for Google News and GDELT, `source_name` is the outlet (1,335 names in the DB for 9 connectors). TED and EUR-Lex are forced to one name each so a mirror site cannot count twice. |
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
(max +1.0), capped at 10. A second bonus (+0.3 for CRM opportunities in the vertical,
+0.3 for pipeline value at or above the median) is coded but reads
`OPPORTUNITY_COUNT_BY_VERTICAL` and `PIPELINE_VALUE_BY_VERTICAL`, which are empty,
so it is always 0 today. The right-to-win call does not read
the linked signals at all — it judges the triple against the portfolio.

On failure it writes `L4 / 0.0` with "do not trust, re-run scoring".

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
weight). It is currently 3.69. **An OS's urgency can move without anything about
that OS changing** — that is the population moving, by design.

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
| `pipeline/config.py` | 582 | Verticals and their search seeds, the closed taxonomy (+ `taxonomy_extensions.json`), all source queries and rate-limit settings, the Orange Business catalog (17 assets, 18 customer references, 5 analyst facts, 12 capability stats), roles, personas, geographies, `STRONG_THRESHOLD`, `quadrant()`. |
| `pipeline/ingest.py` | 611 | One fetcher per source, `safe_run()` isolation, GDELT and Semantic Scholar disk-backed cooldowns (`logs/.source_cooldowns.json`), timestamped run log. |
| `pipeline/analyze.py` | 201 | Theme extraction: `_call_theme_extraction_llm()` (the only LLM boundary), `_classify_themes()` (pure, unit-tested), thin orchestrator; `--from=` resumes after a quota stop. |
| `pipeline/theme_promotion.py` | 46 | Tracks valid themes across runs; feeds `promote`. |
| `pipeline/extend_taxonomy.py` | 204 | Watchlist term → proposal once frequency ≥ 5. |
| `pipeline/taxonomy_validation.py` | 6 | Rejects bare generic terms ("AI") while keeping compounds ("Generative AI"). |
| `pipeline/scoring.py` | 767 | Every formula in §2, the four LLM calls, `--force`, `--from/--to`, `--refresh`, `--recalibrate-*`, `--rescue-fallback`, automatic deterministic refresh of scores older than 3 days. |
| `pipeline/db.py` | 518 | Schema, all queries, duplicate-triple cleanup + `UNIQUE` index, latest-score queries with an `id DESC` tiebreaker. |
| `llm/llm_client.py` | 271 | Provider chain, Groq key rotation, JSON extraction. |
| `radar_cli.py` | 635 | `create`, `promote`, `link`, `summary [--top N]`, `review`, `watchlist`, `calibrate`, `dedupe`, `delete`, `scores`, `all`. |
| `app/streamlit_app.py` | 625 | Polar radar and bubble views, sidebar filters (vertical, domain, horizon, owning team, buyer persona, geography), per-OS breakdown and next actions per role. |
| `radar_cli_top_15.py`, `pipeline/signals_discovery.py` | 515, 58 | Older CLI and its copy of `theme_promotion`, kept on purpose (see `decisions.md`). |
| `scripts/` | | One-off healthcare analysis, run from the repo root. |

### Data model

Seven tables. `signals` is deduplicated on `(source_url, title)`.
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
  (`llama3.2:3b`). A provider with no key is skipped. Temperature 0 everywhere.
- **Closed vocabularies**: the LLM must pick use cases and technologies from the
  taxonomy, domains from `DOMAINS_TAXONOMY`; anything else goes to the watchlist
  (themes) or is set to `None` (domain).
- **Grounding**: strategic relevance and right-to-win prompts embed the real asset
  catalog and cited customer references, and ask the model to name the asset.
- **Fallbacks, never crashes**: a missing key in the response makes the *whole*
  result fall back (5.0 neutral, L4/0, "review manually") rather than mixing
  trusted and untrusted fields. `tests/test_LLM.py` covers `None`, empty dicts,
  missing keys and hallucinated values for every call.

Everything countable — volume, diversity, momentum, urgency, linking — is kept out
of the LLM on purpose.

---

## 5. Testing

72 tests, all passing, no network (`python -m pytest`, under a second).

| File | Tests | Covers |
|---|---|---|
| `tests/test_LLM.py` | 28 | Malformed LLM output across `analyze`, `scoring`, `extend_taxonomy`; generic-term filter; key rotation. |
| `tests/test_scoring_and_db.py` | 44 | Novelty, urgency scaling and caps, latest-score queries, deletes, deterministic refresh (LLM fields untouched, row updated in place), watchlist guard, quadrants, summary header under `--top`, rounded sub-scores, one source name for TED, removal of old non-tech links. |

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
with the same 43 signals counted twice. Fixed with a blocking check plus a
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
12/4/87/30 on the same data.

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
mirrored them (`consultaelectoral.onpe.gob.pe`). Each inflated
`source_diversity`. Both sources now have one fixed name in `ingest.py`.

### 6.9 Eight OS scored with no evidence
OS128–OS135 had been promoted after the last `link`, then scored on zero signals:
attractiveness around 1, right-to-win up to 9.5. Re-linked, then rescored.

### 6.10 A filter that only worked on new data
`NON_TECH_SOURCES` excluded sports and local outlets at link time, but `link` only
adds rows, so older links to those outlets stayed. `link` now removes them first.

### 6.11 A rescore that silently wrote fallbacks over real scores
The first `--force` on OS128–OS135 ran in an environment without the `groq`
package; every provider failed, and the run **exited 0** after writing L4/0 and
neutral 5.0 over the previous values. Caught by reading the log, fixed by
installing `requirements.txt` and re-running. The underlying behaviour is still
there (§8).

---

## 7. The lessons, extracted

1. **The dangerous bug is the one that runs.** 6.1, 6.2, 6.5, 6.7 and 6.8 all
   produced plausible numbers.
2. **A filter or a fix that only applies going forward leaves the old data wrong.**
   6.10, and 6.9 — new OS were never re-linked.
3. **The name of a thing is part of the measurement.** Two names for one source
   is two sources.
4. **A degraded result must not look like a result.** 6.11 is the counterexample
   still in the code.
5. **Constraints belong in the database, not in a warning.** 6.2.
6. **One constant per concept.** 6.5.

---

## 8. Reproducibility, honestly, and open issues

The code and the client summary are in git; **the data is not**. `radar.db` is
ignored, so a clone has no signals, links or scores until the pipeline runs, and a
new ingest will not return the same news. Reproducing the current numbers needs
this exact `radar.db`.

A full run needs `pip install -r requirements.txt` (versions are not pinned) and at
least one working LLM key. The model is called at temperature 0, which makes
repeated calls close, not identical.

| Open issue | Why it matters |
|---|---|
| `link` accepts a single shared keyword | 12 links (6 articles from general outlets) are off-topic: "Natasha Cloud" → Cloud, strikes on Ukraine's "energy sector" → Energy. Requiring 2 keywords would change every link — team decision. |
| Rescore overwrites good values with fallbacks | When no provider answers, `--force` writes 5.0 / L4-0 and exits 0. It should keep the previous value or fail. |
| Right-to-win never reads the signals | By design it judges the triple against the portfolio, but it means evidence cannot move it. |
| Strategic relevance sees only the signal count | Not the titles; two OS with the same triple wording and count get the same prompt. |
| Weights are manual | 30/20/25/10/15 were chosen by hand. `tests/ahp_weights.xlsx` is an AHP pairwise-comparison sheet, not wired into the code. |
| Taxonomy thresholds disagree | `generate_all_proposals()` defaults to 3 but is called with 5; both promotion thresholds are 2. None calibrated. |
| Right-to-win pipeline bonus is dead code | Its two input dicts in `config.py` are empty. |
| Cerebras and SambaNova keys fail | 402 and 401 on 29/09: the fallback chain is effectively Groq → Ollama. |
| `source_diversity` counts publishers | With a cap of 40, an OS linked to many small outlets scores high on diversity. |
| Power BI report | The `.pbix` is binary; its measures cannot be reviewed or tested from the repo. Saving it as `.pbip` would make them diffable. |
| Dependencies unpinned | `requirements.txt` lists names only. |
