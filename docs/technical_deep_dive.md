# Technical deep dive

Everything the [README](../README.md) leaves out: how a signal becomes a scored
Opportunity Space, what every score formula actually measures, which parts are an
LLM judgment and which are arithmetic, and the defects that shaped the code.

Figures come from `radar.db` — signals collected up to 30 August 2026,
re-linked and re-scored on 12 October 2026 — and from a real run of the test suite on 14 October 2026, not
from memory. `radar.db` is committed as a snapshot (§8), so they describe that
database, not whatever a fresh pipeline run would produce. Where a figure could only be obtained by
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
   radar_cli.py link ── >= 2 shared keywords, per vertical,
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
| Opportunity Spaces | 133 (OS001 to OS135; OS052 and OS053 were the removed duplicates): 27 hand-picked seeds from `config.CANDIDATES`, 106 promoted from recurring themes |
| Signal links | 469 under the 2-keyword minimum (3,319 under the old one-keyword rule); 32 OS have none (§8) |
| Recurring themes tracked / promoted | 263 / 124 |
| Watchlist terms / proposals | 235 / 8 (all rejected) |
| Right-to-win levels | L0 12, L1 72, L2 1, L3 48, L4 0 |
| Quadrants (strong / needs capability / moderate / low / insufficient evidence) | 6 / 2 / 62 / 31 / 32 |

---

## 2. Understanding the scores — formulas and how to read them

Computed in `pipeline/scoring.py` on the signals linked to one OS. `n` = number of
linked signals.

### 2.1 Attractiveness (0–10) — five sub-scores, one weighted sum

| Sub-score | Weight | Formula | How to read it |
|---|---|---|---|
| Market signal strength | 39.7% | `min(10, n / 15 × 10)` | Raw volume. 15 = `MARKET_SIGNAL_CAP`, derived by `scripts/derive_caps.py` (below). 6 of the 133 OS reach it; the widest has 37 linked signals, the median 2. |
| Source diversity | 14.1% | `min(10, distinct source_name / 10 × 10)` | Counts distinct *publishers*, not connectors: for Google News and GDELT, `source_name` is the outlet (1,335 names in the DB for 9 connectors). TED and EUR-Lex are forced to one name each so a mirror site cannot count twice: 623 EUR-Lex signals and 185 TED signals sit under one name apiece. The fix only applies going forward, so 6 pre-fix rows still carry `docs.ted.europa.eu`; none of them is linked to an OS, so none reaches this sub-score. |
| Evidence quality | 27.8% | LLM, 0–10, on the first 15 linked titles with their source | "Are these specific, credible, relevant?" 0.0 when the OS has no signal; 5.0 with the justification "LLM scoring unavailable" when no provider answered. |
| Novelty / momentum | 5.5% | share of dated signals in the most recent third of the window [oldest signal, now], × 10 | Acceleration, not just recency. Uses `published_date`, falls back to `collected_at`. Neutral 5.0 below 3 datable signals. |
| Strategic relevance | 13.0% | LLM, 0–10, against the Orange Business asset catalog; +0.5 for Defense, Aerospace & Defense, Healthcare | The LLM sees the triple and the *number* of signals, not their titles. The bonus reflects Orange running a dedicated division for those verticals. |

```
total_score = Σ weight_k · sub_score_k     (weights sum to 1)
```

The weights are the AHP priority vector of the pairwise judgments in
`pipeline/ahp_weights.xlsx`, computed by `pipeline/ahp.py` (normalised row
geometric means, the sheet's own method; the principal eigenvector agrees to
within 0.2 points). Consistency ratio 0.021: the code refuses judgments at
CR >= 0.10. The hand-set 30/20/25/10/15 (market/diversity/evidence/novelty/
relevance) stays available as the `manual` weighting:
`python -m pipeline.scoring --weights=manual`, or `WEIGHTING` in
`pipeline/scoring.py`. 59.3% of the weight is deterministic, 40.7% an LLM
judgment (manual: 60/40). AHP turns the team's judgments into consistent
weights; it does not fit them to outcomes (see §8).

**The two caps are derived, not chosen.** A cap only means something relative to
how many signals `link` gives an OS, so `scripts/derive_caps.py` keeps each cap at
the position the one-keyword caps (56 signals, 40 publishers) held under the
one-keyword rule. The position is the mid-rank percentile, (OS below + half the OS
equal) / 133, which stays defined when OS tie at the cap; the new cap is the
inclusive quantile of the two-keyword distribution at that percentile, rounded.
Both distributions are replayed from the signals through
`radar_cli.matching_signals()`, so the result does not depend on which links the
database stores. On the committed signals:

| Cap | Old | Percentile of the old cap | New distribution (min / median / max) | New cap | p90 (cross-check) |
|---|---|---|---|---|---|
| `MARKET_SIGNAL_CAP` (signals) | 56 | 0.959 (11 OS tied at 56: `link` cuts at `top_n` 56, so the old distribution is censored there) | 0 / 2 / 37 | **15** | 8.8 |
| `SOURCE_DIVERSITY_CAP` (publishers) | 40 | 0.947 (7 OS above, none at 40) | 0 / 1 / 28 | **10** | 8.0 |

The cap is no longer equal to `top_n`. `tests/test_derive_caps.py` fails if the
constants in `pipeline/scoring.py` drift from what the script derives from
`radar.db`.

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
(max +1.0), capped at 10 — the only bonus there is (`customer_reference_bonus()`;
the justification names the reference count, where it used to say "CRM customer
overlap" although no CRM data is read). A second bonus (+0.3 for CRM
opportunities in the vertical, +0.3 for pipeline value at or above the median) used
to be coded, but it read `OPPORTUNITY_COUNT_BY_VERTICAL` and
`PIPELINE_VALUE_BY_VERTICAL`, both empty, so it was always 0 while the
justification still advertised it; the function and its two config dicts are gone.
The right-to-win call does not read the linked signals at all — it judges the
triple against the portfolio.

`CUSTOMER_REFERENCE_BONUS` (0.5) and `CUSTOMER_REFERENCE_BONUS_CAP` (1.0) in
`pipeline/scoring.py` are hand-set, and stay so: fitting them would need won/lost
outcomes per OS, which the project does not have, so no value is "justified by
the data" and none is pretended to be. What the data does show is how much rides
on the choice. `python scripts/rtw_bonus_sensitivity.py` reads the bonus applied
to each stored score back from its justification, recovers the LLM's own score,
and re-applies other values (cap kept at 1.0), with no LLM call. On the database
re-scored on 12 October (133 OS; 77 carry a bonus, 57 at +1.0 and 20 at +0.5):

| Bonus per reference | RTW score changes | Crosses RTW 7 | Quadrant changes | Strong | Level changes |
|---|---|---|---|---|---|
| 0 (no bonus) | 77 | 6 | 6 | 6 | 0 |
| 0.25 | 50 | 2 | 2 | 6 | 0 |
| **0.5 (current)** | 0 | 0 | 0 | 6 | 0 |
| 0.75 | 20 | 0 | 0 | 6 | 0 |

The portfolio level (L0–L4) is the LLM's and never moves with the bonus, and no
value changes the strong count. At 0.25 two OS change quadrant (OS054 and
OS107, both L3 at 7.0); dropping the bonus moves six, all L3 OS the bonus lifts
from 6.5 to 7 or more, none of them strong. Five stored scores were clipped at 10, so their LLM score is only a
lower bound; none is near 7. The same references are also listed in the
right-to-win prompt, so the bonus may count them a second time — a reason to
keep it small, not a measurement.

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
weight). On the database re-linked on 12 October 2026 it is 1.75: with 469 links
instead of 3,319 each OS weighs fewer urgent signals (it was 3.68 on 5 October
and 3.69 on 30 September, on the old links). It also drifts with the calendar on
a frozen snapshot: buying signals lose weight as they age. **An OS's urgency can
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

A missing score yields no quadrant rather than "low". An OS with no linked
signal is put in a fifth category, **insufficient evidence**, before the
grid is read: its attractiveness is computed on nothing, so a near-zero
score there says the radar found no article, not that the market is weak.
On the current radar.db that is 32 OS, 23 of which the grid alone would
call "moderate market" and 9 "low on both". The Power BI model applies the
same rule (`Insufficient Evidence`) from the `linked_signals` column of
`latest_scores.csv`.

---

## 3. Module reference

| Module | Lines | What it owns |
|---|---|---|
| `pipeline/config.py` | 599 | Verticals and their search seeds, the closed taxonomy (+ `taxonomy_extensions.json`), all source queries and rate-limit settings, the Orange Business catalog (17 assets, 18 customer references, 5 analyst facts, 12 capability stats), roles, personas, geographies, `STRONG_THRESHOLD`, `quadrant()`, `TAXONOMY_PROPOSAL_THRESHOLD`. |
| `pipeline/ingest.py` | 627 | One fetcher per source, `safe_run()` isolation, RSS/Atom feeds downloaded with a timeout (`FEED_TIMEOUT_SECONDS`), GDELT and Semantic Scholar disk-backed cooldowns (`logs/.source_cooldowns.json`), timestamped run log. |
| `pipeline/analyze.py` | 201 | Theme extraction: `_call_theme_extraction_llm()` (the only LLM boundary), `_classify_themes()` (pure, unit-tested), thin orchestrator; `--from=` resumes after a quota stop. |
| `pipeline/theme_promotion.py` | 46 | Tracks valid themes across runs; feeds `promote`. |
| `pipeline/extend_taxonomy.py` | 212 | Watchlist term → proposal once frequency ≥ `TAXONOMY_PROPOSAL_THRESHOLD` (5, in `config.py`). |
| `pipeline/taxonomy_validation.py` | 6 | Rejects bare generic terms ("AI") while keeping compounds ("Generative AI"). |
| `pipeline/ahp.py` | 98 | AHP priority vector and consistency ratio from `pipeline/ahp_weights.xlsx`; refuses CR >= 0.10. |
| `pipeline/scoring.py` | 924 | Every formula in §2, the four LLM calls, `--force`, `--from/--to`, `--refresh`, `--recalibrate-*`, `--rescue-fallback`, `--prune-scores`, automatic deterministic refresh of scores older than 3 days. |
| `pipeline/db.py` | 757 | Schema, all queries, duplicate-triple cleanup + `UNIQUE` index, latest-score queries with an `id DESC` tiebreaker, foreign-key repair, append-only score history. |
| `llm/llm_client.py` | 234 | Provider chain, Groq key rotation, JSON extraction. |
| `radar_cli.py` | 667 | `create`, `promote`, `link`, `summary [--top N] [--output PATH]`, `review`, `watchlist`, `themes`, `calibrate`, `dedupe`, `delete`, `scores`, `all`. |
| `app/streamlit_app.py` | 704 | Polar radar and bubble views, sidebar filters (vertical, domain, horizon, owning team, buyer persona, geography), per-OS breakdown and next actions per role. |
| `radar_cli_top_15.py`, `pipeline/signals_discovery.py` | 518, 58 | Older CLI and its copy of `theme_promotion`, kept on purpose (see `decisions.md`). |
| `scripts/` | | One-off healthcare analysis, run from the repo root. |
| `scripts/derive_caps.py` | 110 | Re-derives `MARKET_SIGNAL_CAP` and `SOURCE_DIVERSITY_CAP` from the link volume of both keyword rules (§2.1); read-only. |
| `scripts/rtw_bonus_sensitivity.py` | 110 | Re-applies other customer-reference bonus values to the stored right-to-win scores and counts what moves (§2.2); read-only, no LLM. |
| `scripts/export_powerbi.py` | 226 | Exports `radar.db` (opened read-only) to the six CSV files the Power BI project reads, reports unparseable dates, and warns when the model's `DataFolder` points elsewhere; run it before a Refresh. |

### Data model

Eight tables in `SCHEMA`, plus two history tables. `signals` is deduplicated on
`(source_url, title)`. `opportunity_spaces` carries the triple, the enrichment
fields and a unique label; `opportunity_signals` is the many-to-many link.
`scores` and `right_to_win_scores` hold **one current row per OS**:
`insert_score()` and `insert_right_to_win_score()` update in place (decided 29/09,
`decisions.md`), and every read of a score reads those. `watchlist_terms`,
`recurring_themes` and `proposals` drive taxonomy growth.

**Score history** (`score_history`, `right_to_win_history`, `HISTORY_SCHEMA` in
`pipeline/db.py`, added 12/10). Every accepted write is also appended, in the
same transaction, as a copy of the row just written, with the OS label and triple
(`wipe` resets the id sequence, so an id alone can be reused) and
`origin = 'write'`. A write refused by the fallback guard (§6.11) leaves no
history row. The tables are created by `ensure_history_tables()` on the first
score write or by `init_db()`, and seeded once with the current rows as
`origin = 'baseline'`, so the first rescore after the upgrade already has a
previous value to compare with. They carry no foreign key: a history outlives the
deletion of its OS, and no delete path has to change. Nothing reads them yet
except `get_score_history()`; no number on the dashboard or in the summaries
comes from them. The committed `radar.db` holds them since the 12 October re-score:
`score_history` has 133 baseline rows and 665 writes, `right_to_win_history` 133
and 136.

---

## 4. Where the LLM is used, and how it is contained

Five kinds of call, each a **single structured call with a fixed prompt** that must
return one JSON object: theme extraction (`analyze.py`), evidence quality,
strategic relevance, right-to-win, and enrichment (role, buyer persona, 1–3
geographies, horizon, domain, one next action per role). No agent, no tool use, no
multi-step loop.

- **Provider chain** (`LLM_PROVIDER=auto`): Groq (`openai/gpt-oss-120b`, up to 5
  keys, rotated only on 429/quota errors) → SambaNova → local Ollama
  (`llama3.2:3b`). A provider with no key is skipped. Temperature 0 on Groq and SambaNova;
  the Ollama call sends no temperature, so it runs at the model's default.
- **Closed vocabularies**: the LLM must pick use cases and technologies from the
  taxonomy, domains from `DOMAINS_TAXONOMY`; anything else goes to the watchlist
  (themes) or is set to `None` (domain).
- **Grounding**: strategic relevance and right-to-win prompts embed the real asset
  catalog and cited customer references, and ask the model to name the asset.
- **Fallbacks, never crashes**: a missing *required* key (`score`, `portfolio_distance`,
  `role`) makes the whole result fall back (5.0 neutral, L4/0, "review manually").
  Other keys get a silent default (`right_to_win_score` 0, `horizon` "Later").
  `portfolio_distance` is checked against L0–L4 (an invented level becomes L4);
  a word, `nan` or `inf` where a score was asked for gives the marked neutral, and
  a finite score outside 0–10 is clamped. `tests/test_LLM.py` covers `None`, empty
  dicts, missing keys, invented levels, words and out-of-range scores.
- **Untrusted titles**: the evidence-quality prompt fences the signal titles in a
  `<signals>` block declared to be data, never instructions, and defangs any
  spelling of that tag inside a title.

Everything countable — volume, diversity, momentum, urgency, linking — is kept out
of the LLM on purpose.

---

## 5. Testing

208 tests, all passing on 14 October 2026, no network (`python -m pytest`, a few
seconds). "No network" is now enforced rather than reviewed: an autouse fixture in
`conftest.py` makes `socket.connect` / `create_connection` raise, so a test that
forgets to mock its feed or LLM call fails with an explicit message instead of
reaching the internet.

| File | Tests | Covers |
|---|---|---|
| `tests/test_LLM.py` | 58 | Malformed LLM output across `analyze`, `scoring`, `extend_taxonomy`; generic-term filter; key rotation; non-numeric scores ("high") coerced to a marked neutral instead of raising; `nan`/`inf` sent to the fallback and out-of-range scores clamped to 0–10; signal titles fenced and unable to close the fence; `portfolio_distance` validated against L0–L4; the network block itself. |
| `tests/test_export_powerbi.py` | 19 | Power BI export: date formats to UTC, missing stays empty (never 0), unparseable dates reported, stable header with no rows, HTML summaries to text, `DataFolder` mismatch warning, read-only DB, the linked-signal count and the model's Insufficient Evidence quadrant. |
| `tests/test_scoring_and_db.py` | 57 | Novelty, urgency scaling and caps, latest-score queries, deletes, deterministic refresh (LLM fields untouched, row updated in place), watchlist guard, quadrants (including insufficient evidence), summary header under `--top`, rounded sub-scores, urgency wording in the summary, one source name for TED, removal of old non-tech links, the refusal to overwrite a real score with a fallback, and the feed download timeout. |
| `tests/test_dashboard.py` | 15 | Dashboard, through Streamlit's `AppTest`: missing `radar.db` reported as an error with the pipeline hint, the AI-generated disclosure rendered on the page, the not-enough-evidence count in the overview, each tab opened exactly once, an all-NaN top-opportunity selection, and which `source_url` values become links. |
| `tests/test_score_history.py` | 8 | Score history: every accepted write appended while `scores` keeps one row, a refused fallback leaves no trace, right-to-win history, the one-time baseline, history surviving a deleted OS, `init_db()` on a new database, and latest-score reads unchanged on a copy of the committed `radar.db`. |
| `tests/test_taxonomy_threshold.py` | 5 | One proposal threshold: both defaults read `TAXONOMY_PROPOSAL_THRESHOLD`, a call without a threshold proposes the same terms as the pipeline, an explicit threshold still wins. |
| `tests/test_foreign_keys.py` | 4 | The replayed migration that left child tables pointing at a dropped parent, the repair, foreign keys on every connection, no violation left in a copy of `radar.db`. |
| `tests/test_network_guard.py` | 4 | The socket block itself. |
| `tests/test_link_keywords.py` | 7 | `link` needs 2 distinct shared keywords; links below the minimum are pruned for the OS they are weak for only; `calibrate` counts with the same minimum. |
| `tests/test_ahp_weights.py` | 10 | AHP weights of the committed sheet and their CR; reciprocals rebuilt; inconsistent or non-positive judgments refused; `manual` kept as a named option; `--weights` refuses an unknown name. |
| `tests/test_derive_caps.py` | 6 | Cap derivation: mid-rank percentile with ties, inclusive quantile, both link rules replayed without writing, and the constants in `scoring.py` equal to the derivation on `radar.db`. |
| `tests/test_llm_chain.py` | 8 | Provider chain Groq → SambaNova → Ollama: order, fall-through, last error raised, one named provider alone, no Cerebras left. |
| `tests/test_rtw_bonus.py` | 7 | Customer-reference bonus: documented defaults, per-reference and cap, configurable value reaching the stored score, and the sensitivity counts. |

Network calls are mocked (`feedparser.parse` is monkeypatched; LLM calls are
monkeypatched at `scoring.get_llm_json`). CI (`.github/workflows/tests.yml`)
runs the suite on Python 3.12 on every push.

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
those two figures are not re-derivable). On the 30 September scores the same
comparison read 11/2/61/59 at a threshold of 8 against 16/5/77/35 at 7; on the
scores of 12 October it reads 4/3/58/68 against 6/2/85/40 — the shape of the
point, not the exact counts, is what carried.

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
project is tested against; `streamlit` is a `>=1.65.0` floor, since Streamlit Cloud
upgrades its own runtime) and at least one working LLM key. The model is called at
temperature 0, which makes repeated calls close, not identical.

| Open issue | Why it matters |
|---|---|
| `link` now needs 2 shared keywords — and 32 OS have none | `MIN_KEYWORD_OVERLAP = 2` in `radar_cli.py`. On a seeded, hand-checked sample of 120 committed links (`docs/link_sample_2026-10-12.csv`), 31 of 80 single-keyword links were off-topic against 3 of 40 at two or more; "1 keyword + same vertical" kept 13 of 40 off-topic, "Natasha Cloud" → Cloud among them. Re-linking a copy of the committed database: 3,319 → 469 links. Extrapolating the sample rates to the 2,850 dropped links, about 1,070 off-topic links go and about 1,780 legitimate ones go with them. **32 of 133 OS end with no link at all**, OS020 (the top OS by attractiveness) among them — mostly OS whose use case and technology repeat one generic word ("Cloud Infrastructure Modernization × Cloud"). The median OS keeps 2 signals; the market and diversity caps were re-derived for that volume (§2.1). `radar.db` was re-linked and re-scored with it on 12 October: strong 16 → 6, mean attractiveness 5.25 → 3.33; OS020 fell from 9.19 to 1.04 (no link left), OS083 from 7.59 to 0.26, OS004 from 7.49 to 0.65. |
| Fallback scores are kept until someone re-runs | A refused write (§6.11) leaves yesterday's real score in place and exits non-zero, which is the right default but means the radar can hold a score older than its signals. `--rescue-fallback` re-scores the spaces still on a fallback; none of the 133 is on one in the committed database. One OS carries older LLM sub-scores instead: for OS106 Groq answers the evidence-quality prompt with one JSON object per source instead of `{score, justification}`, on every try at temperature 0 (Ollama failed too), so its evidence quality (4.0) and strategic relevance (5.5) are the 30 September values; its deterministic sub-scores and total (4.35) were refreshed on 12 October. The prompt asks for one object clearly, so it was left unchanged: rewording it would move every OS's evidence score. |
| Right-to-win never reads the signals | By design it judges the triple against the portfolio, but it means evidence cannot move it. |
| Strategic relevance sees only the signal count | Not the titles; two OS with the same triple wording and count get the same prompt. |
| Weights come from AHP judgments, not outcomes | `pipeline/ahp_weights.xlsx` (moved from `tests/`, which the Docker image excludes) now sets the weights: 39.7/27.8/14.1/13.0/5.5 for market/evidence/diversity/relevance/novelty, CR 0.021. The judgments are still a team's opinion, made consistent, not fitted to any win/loss data, which the project does not have. Re-totalled from the stored sub-scores of the 133 OS, the top 15 keep the same members (4 adjacent swaps: OS001/OS013, OS134 9th from 11th, OS029, OS017/OS014); 103 OS change rank, by at most 11 places; totals move by at most 0.42; strong 16 -> 19 (OS034, OS065, OS108 cross 7). The re-score of 12 October applied them. |
| Taxonomy thresholds are not calibrated | A watchlist term becomes a proposal at `TAXONOMY_PROPOSAL_THRESHOLD` = 5 sightings; a valid theme becomes an OS at `RECURRING_THEME_PROMOTION_THRESHOLD` = 2; `analyze.py` lists watchlist terms "ready for review" from `WATCHLIST_PROMOTION_THRESHOLD` = 2, a display filter only. All hand-set, none calibrated. The proposal threshold used to be 3 as the default of `generate_all_proposals()` and 5 at its only call; since 12/10 both read the one constant, kept at 5, so nothing the pipeline does changed. On the committed `radar.db`, 8 watchlist terms are at 5 or more and 33 at 3 or more. |
| Right-to-win calibration is one hand-set bonus | +0.5 per customer reference in the vertical, capped at +1.0: now two named parameters (`CUSTOMER_REFERENCE_BONUS`, `CUSTOMER_REFERENCE_BONUS_CAP`), still not fitted — there are no won/lost outcomes to fit them to. `scripts/rtw_bonus_sensitivity.py` shows what rides on the value: on the re-scored database 2 of 133 OS change quadrant at 0.25, none at 0.75, 6 with no bonus, and the strong count never moves (§2.2). A new value takes effect when right-to-win is re-scored (`--recalibrate-right-to-win`, an LLM run). |
| SambaNova is rate-limited | One test call on 12/10 got 429 (the key is accepted; it got 401 on 29/09), so in practice the chain often falls from Groq to Ollama. The 12/10 re-score ran on Groq: all five keys answered. |
| `source_diversity` counts publishers | With a cap of 10, an OS linked to many small outlets scores high on diversity. 8 of the 133 OS are at or above the cap; the widest has 28 distinct publishers, the median 1. |
| Power BI refresh path | Power Query has no relative file paths, so the `DataFolder` parameter is an absolute path to one machine; `export_powerbi.py` now warns and prints the right value when it does not match. The CSV export must be re-run by hand after each pipeline run. The quadrant threshold 7 is restated in the model instead of read from `STRONG_THRESHOLD`. |
| Right-to-win is not stable across runs | Two scoring runs on the same linked signals (29/09 and 30/09) moved 38 of 133 OS to another level, 30 of them between L1 and L3. The top 15 by attractiveness kept the same members, in a different order. Re-measured from `right_to_win_history` on 12/10: right-to-win reads neither the links nor the weights and its prompt did not change, yet against the 30/09 baseline 29 of 133 OS changed level, 23 of them between L1 and L3. Same order of magnitude. |
| Dependencies pinned, except Streamlit | `requirements.txt` pins exact versions for the other eight packages; `streamlit>=1.65.0` is a floor because Streamlit Cloud upgrades its own runtime. A pin is the version the project was tested against, not a version anyone validated as the best. |
