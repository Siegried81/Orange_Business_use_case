# Design decisions

The decisions that still hold, with the reason and the condition for revisiting each. History of how the code got here is in git.

## Scoring

**Two axes, kept apart (2026-10-16).** Attractiveness (market evidence) and Right-to-Win (fit with the public Orange Business portfolio) are never summed. Urgency is shown separately. The dashboard's "Top Opportunity" ranks on attractiveness alone, because right-to-win is an LLM classification against public data and should not decide which space comes first. Revisit when right-to-win is scored against internal data.

**Attractiveness weights come from an AHP sheet (2026-10-12).** `pipeline/ahp.py` reads the pairwise judgements in `pipeline/ahp_weights.xlsx`, computes the priority vector and the consistency ratio, and refuses a ratio of 0.10 or more. The hand-set weights stay available (`python -m pipeline.scoring --weights=manual`). AHP makes judgements consistent, it does not validate them; revisit if won/lost outcomes become available. The sheet lives in `pipeline/` because the Docker image excludes `tests/`.

**Caps are derived, not chosen (2026-10-12).** `MARKET_SIGNAL_CAP` and `SOURCE_DIVERSITY_CAP` are the number of linked signals and distinct publishers at which a sub-score reaches 10. `scripts/derive_caps.py` recomputes them from the link volume, and `tests/test_derive_caps.py` fails if the constants drift. Re-run the script whenever the link rule or `top_n` changes.

**The right-to-win customer-reference bonus stays hand-set (2026-10-12)** (`CUSTOMER_REFERENCE_BONUS`, capped by `CUSTOMER_REFERENCE_BONUS_CAP`). Fitting it needs an outcome per space, which the project does not have. `scripts/rtw_bonus_sensitivity.py` shows what moves if the value changes, without calling the LLM.

**One quadrant threshold (2026-09-29, insufficient evidence added 2026-10-12).** `STRONG_THRESHOLD = 7` in `pipeline/config.py`, used by every view; two thresholds once made a space "strong" in one place and not in another. A space with no linked signal is "insufficient evidence", not "low": its attractiveness is computed on nothing.

**Scores are one current row per space, plus an append-only history (2026-10-12).** `scores` and `right_to_win_scores` hold one row each and every read uses them. `score_history` and `right_to_win_history` receive a copy of each accepted write, with no foreign key so that a history outlives its space. Revisit when the history grows large or the dashboard shows score evolution.

**One constant per concept (2026-10-12).** The taxonomy proposal threshold is `TAXONOMY_PROPOSAL_THRESHOLD = 5` and is read by every entry point; two defaults (3 and 5) once proposed different terms from the same data.

## Linking

**A signal links to a space only with 2 shared keywords (2026-10-12)** (`MIN_KEYWORD_OVERLAP`, `radar_cli.py`). One shared word linked unrelated articles ("Natasha Cloud" to two cloud spaces). On a hand-labelled sample of 120 links (`docs/link_sample_2026-10-12.csv`), 31 of 80 single-keyword links were off-topic against 3 of 40 at two or more. The price is that many spaces end up with no link, which the dashboard reports as "insufficient evidence". Revisit with a looser rule measured to stay precise (summary matching, or a minimum relative to the space's own keyword count).

**Link runs before score (2026-09-29)**, so each space is scored on its own signals and not on everything collected for its vertical.

## LLM

**Provider chain Groq, SambaNova, Ollama (2026-09-29, Cerebras dropped 2026-10-12)** (`LLM_PROVIDER=auto`), with Groq keys 1 to 5 rotated on rate limits. Cerebras was dropped when its free tier stopped working.

**LLM output is untrusted (2026-10-13, extended 2026-11-08 and 2026-11-20).** Scores are clamped to 0-10, `nan`/`inf` and non-numeric answers go to a documented neutral fallback, and signal titles are fenced in a `<signals>` block the system prompt declares to be data. A fallback result must never look like a result: the database refuses to overwrite a real score or real enrichment with a fallback, and a scoring run with no answering provider exits non-zero. Role, buyer persona, horizon and domain are checked against their lists and stored as `None` when off them; geography is reduced to region names from `GEOS` by `normalize_geography()`, when written and when the dashboard loads (the prompt lists countries in parentheses and the model returned the whole label, which split into fragments in the filter). Constraints belong in the database, not in a warning.

## Data and storage

**`radar.db` is committed (2026-10-01)** because Streamlit Cloud and Render deploy from the repository and cannot run the pipeline. It is a snapshot that changes only when someone re-runs the pipeline and commits the result.

**Foreign keys are on (2026-10-10)** (`PRAGMA foreign_keys = ON` in `get_connection()`), and `init_db()` repairs child tables whose FOREIGN KEY names a table that no longer exists. If a migration renames a parent table, set `PRAGMA legacy_alter_table = ON` around the rename.

**Paths are anchored to the repo (2026-11-08).** `DB_PATH` and the log folder are absolute, derived from the file location, so opening the database from another working directory never creates an empty `radar.db`. There is no environment override, so the pipeline and the scripts cannot read two different files.

**Feed errors are failures (2026-11-08).** `_parse_feed` raises on an HTTP error status; an error page used to parse as a feed with no entries and read as "nothing new". One source name per source (TED and EUR-Lex are forced to one each) keeps `source_diversity` from counting a mirror twice (2026-09-29).

**Requests are paced (2026-11-20).** `ARXIV_SLEEP_SECONDS` and `GOOGLE_NEWS_SLEEP_SECONDS` were declared and never read, so both sources were queried back to back. `run_full_refresh()` now waits after each request, about two minutes more over a full ingest.

**Urgency always scales on the scored population (2026-11-08).** `--rescue-fallback` used the static `URGENCY_CAP` while every other path used the 95th percentile; it now uses `compute_urgency_scaling_point`.

**A fallback never overwrites real enrichment (2026-11-08).** Enrichment carrying `FALLBACK_MARKER` only fills empty fields, the rule `insert_score()` already applies to scores.

## Delivery

**Power BI reads a CSV export (2026-09-30)** (`scripts/export_powerbi.py`), because Power BI has no SQLite connector. The report is a PBIP project, so measures and visuals are diffable text. Its `DataFolder` parameter is an absolute local path and the quadrant threshold is restated in the model; revisit if the report must refresh elsewhere. The export also reduces `geography` to region names, like the dashboard (2026-11-22).

**The dashboard draws the radar on cartesian axes (2026-10-16)**, because Streamlit's `on_select` never reports a click on a `Scatterpolar` trace. Revisit if Streamlit adds polar selection events.

**An Airflow DAG runs the same stages as `radar_cli.py all`**, one `@daily` task per stage, with `catchup=False` (2026-11-04). Airflow lives in `requirements-airflow.txt` (Python 3.12, own environment) because its dependency tree conflicts with the pipeline's pins. CI runs the DAG tests against a stub Airflow in the main job and against Airflow 2.10 in a second job.

**The Docker image carries no test tooling (2026-11-22).** The `Dockerfile` has a `test` stage (adds `requirements-dev.txt`) used only by CI, and a last `prod` stage built by default and by Render. Python is 3.12, the version CI tests.

## Cleanup

**Duplicates and dead code removed (2026-11-22).** `radar_cli_top_15.py`, `pipeline/signals_discovery.py`, `pipeline/latest_scores.py` and the one-off healthcare scripts are gone, which replaces the "kept on purpose" line of 2026-09-29. `radar_cli.py summary --top 15 --output opportunity_spaces_summary_top_15.md` writes the same file and `radar_cli.py scores` replaces `latest_scores.py`.

**Generated summaries are not tracked (2026-11-22).** `opportunity_spaces_summary*.md` are ignored; regenerate them with `radar_cli.py summary`.

**Partner tiers stay out of the prompts (2026-11-22).** `PARTNER_TIERS` in `pipeline/config.py` is read by nothing. Using it in the right-to-win prompt needs a full LLM rescore and would move scores, levels and quadrants. Listed in the README limitations.

**`SIGNAL_TYPES` is kept (2026-11-22)** as the declared list of signal types; a test checks every type the fetchers assign is in it.

## Lessons

- The dangerous bug is the one that runs and produces a plausible number; tests should pin numbers, not only "does not crash".
- A fix that only applies going forward leaves old data wrong; re-link and re-score after changing a rule.
- The name of a thing is part of the measurement: two names for one source count as two sources.
