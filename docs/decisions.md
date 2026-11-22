# Design decisions

The decisions that still hold, with the reason and the condition for revisiting each. History of how the code got here is in git.

## Scoring

**Two axes, kept apart.** Attractiveness (market evidence) and Right-to-Win (fit with the public Orange Business portfolio) are never summed. Urgency is shown separately. The dashboard's "Top Opportunity" ranks on attractiveness alone, because right-to-win is an LLM classification against public data and should not decide which space comes first. Revisit when right-to-win is scored against internal data.

**Attractiveness weights come from an AHP sheet.** `pipeline/ahp.py` reads the pairwise judgements in `pipeline/ahp_weights.xlsx`, computes the priority vector and the consistency ratio, and refuses a ratio of 0.10 or more. The hand-set weights stay available (`python -m pipeline.scoring --weights=manual`). AHP makes judgements consistent, it does not validate them; revisit if won/lost outcomes become available. The sheet lives in `pipeline/` because the Docker image excludes `tests/`.

**Caps are derived, not chosen.** `MARKET_SIGNAL_CAP` and `SOURCE_DIVERSITY_CAP` are the number of linked signals and distinct publishers at which a sub-score reaches 10. `scripts/derive_caps.py` recomputes them from the link volume, and `tests/test_derive_caps.py` fails if the constants drift. Re-run the script whenever the link rule or `top_n` changes.

**The right-to-win customer-reference bonus stays hand-set** (`CUSTOMER_REFERENCE_BONUS`, capped by `CUSTOMER_REFERENCE_BONUS_CAP`). Fitting it needs an outcome per space, which the project does not have. `scripts/rtw_bonus_sensitivity.py` shows what moves if the value changes, without calling the LLM.

**One quadrant threshold.** `STRONG_THRESHOLD = 7` in `pipeline/config.py`, used by every view; two thresholds once made a space "strong" in one place and not in another. A space with no linked signal is "insufficient evidence", not "low": its attractiveness is computed on nothing.

**Scores are one current row per space, plus an append-only history.** `scores` and `right_to_win_scores` hold one row each and every read uses them. `score_history` and `right_to_win_history` receive a copy of each accepted write, with no foreign key so that a history outlives its space. Revisit when the history grows large or the dashboard shows score evolution.

**One constant per concept.** The taxonomy proposal threshold is `TAXONOMY_PROPOSAL_THRESHOLD = 5` and is read by every entry point; two defaults (3 and 5) once proposed different terms from the same data.

## Linking

**A signal links to a space only with 2 shared keywords** (`MIN_KEYWORD_OVERLAP`, `radar_cli.py`). One shared word linked unrelated articles ("Natasha Cloud" to two cloud spaces). On a hand-labelled sample of 120 links (`docs/link_sample_2026-10-12.csv`), 31 of 80 single-keyword links were off-topic against 3 of 40 at two or more. The price is that many spaces end up with no link, which the dashboard reports as "insufficient evidence". Revisit with a looser rule measured to stay precise (summary matching, or a minimum relative to the space's own keyword count).

**Link runs before score**, so each space is scored on its own signals and not on everything collected for its vertical.

## LLM

**Provider chain Groq, SambaNova, Ollama** (`LLM_PROVIDER=auto`), with Groq keys 1 to 5 rotated on rate limits. Cerebras was dropped when its free tier stopped working.

**LLM output is untrusted.** Scores are clamped to 0-10, `nan`/`inf` and non-numeric answers go to a documented neutral fallback, and signal titles are fenced in a `<signals>` block the system prompt declares to be data. A fallback result must never look like a result: the database refuses to overwrite a real score or real enrichment with a fallback, and a scoring run with no answering provider exits non-zero. Constraints belong in the database, not in a warning.

## Data and storage

**`radar.db` is committed** because Streamlit Cloud and Render deploy from the repository and cannot run the pipeline. It is a snapshot that changes only when someone re-runs the pipeline and commits the result.

**Foreign keys are on** (`PRAGMA foreign_keys = ON` in `get_connection()`), and `init_db()` repairs child tables whose FOREIGN KEY names a table that no longer exists. If a migration renames a parent table, set `PRAGMA legacy_alter_table = ON` around the rename.

**Paths are anchored to the repo.** `DB_PATH` and the log folder are absolute, derived from the file location, so opening the database from another working directory never creates an empty `radar.db`. There is no environment override, so the pipeline and the scripts cannot read two different files.

**Feed errors are failures.** `_parse_feed` raises on an HTTP error status; an error page used to parse as a feed with no entries and read as "nothing new". One source name per source (TED and EUR-Lex are forced to one each) keeps `source_diversity` from counting a mirror twice.

## Delivery

**Power BI reads a CSV export** (`scripts/export_powerbi.py`), because Power BI has no SQLite connector. The report is a PBIP project, so measures and visuals are diffable text. Its `DataFolder` parameter is an absolute local path and the quadrant threshold is restated in the model; revisit if the report must refresh elsewhere.

**The dashboard draws the radar on cartesian axes**, because Streamlit's `on_select` never reports a click on a `Scatterpolar` trace. Revisit if Streamlit adds polar selection events.

**An Airflow DAG runs the same stages as `radar_cli.py all`**, one `@daily` task per stage, with `catchup=False`. Airflow lives in `requirements-airflow.txt` (Python 3.12, own environment) because its dependency tree conflicts with the pipeline's pins. CI runs the DAG tests against a stub Airflow in the main job and against Airflow 2.10 in a second job.

**The Docker image carries no test tooling.** The `Dockerfile` has a `test` stage (adds `requirements-dev.txt`) used only by CI, and a last `prod` stage built by default and by Render. Python is 3.12, the version CI tests.

## Lessons

- The dangerous bug is the one that runs and produces a plausible number; tests should pin numbers, not only "does not crash".
- A fix that only applies going forward leaves old data wrong; re-link and re-score after changing a rule.
- The name of a thing is part of the measurement: two names for one source count as two sources.
- Generated files (`opportunity_spaces_summary.md`) are not committed; regenerate them with `radar_cli.py summary`.
