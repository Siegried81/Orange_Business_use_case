# Decision log

One dated entry per decision: what, why, what changed in the code, and when to
revisit it. Append only — never rewrite a past entry.

---

## 2026-09-29 — Restore the missing `llm/` package

- **What:** `llm/__init__.py` and `llm/llm_client.py` restored from the
  28-15h project backup.
- **Why:** `pipeline/analyze.py` and `pipeline/scoring.py` import
  `llm.llm_client.get_llm_json`, but the package was missing from the repo,
  so the CLI, the pipeline and the whole test suite failed at import time
  (0 tests collected, 2 collection errors).
- **Code change:** two files added, no existing file modified. The restored
  client supports Groq (keys `GROQ_API_KEY`, `_2`, `_3`), Cerebras, SambaNova
  and Ollama, matching the variables in `.env`.
- **Result:** test suite went from 2 collection errors to 59 passed, 1 failed
  (see next entry).
- **Revisit if:** a newer `llm_client.py` turns up (e.g. on the GitHub remote).

## 2026-09-29 — Open question: score history vs overwrite (decided below)

- **What:** `tests/test_scoring_and_db.py::TestRefreshDeterministicScores::test_inserts_a_new_row_rather_than_overwriting`
  fails. `pipeline/db.py::insert_score` UPDATEs the existing row when an
  opportunity space already has a score, while the test (and the README's
  "`id DESC` tiebreaker on every latest-score query") assume one new row per
  scoring run.
- **Why it matters:** it decides whether score history is kept. With
  UPDATE, "momentum over time" cannot be reconstructed from `scores`.
- **Code change:** none yet — needs a team decision.
- **Revisit if:** decided either way; then fix the code or the test, not both.

## 2026-09-29 — Scores are overwritten, not kept as history

- **What:** keep `insert_score()` updating one row per opportunity space.
- **Why:** `clean_scores()` and `get_opportunity_spaces_with_old_scores()`
  already assume a single row, and nothing reads score history
  (`novelty_momentum` comes from signal dates, not past scores). Keeping
  history would have meant changing four places for unused data.
- **Code change:** the refresh test now asserts one row with the new total;
  README "Key challenges" wording aligned.
- **Revisit if:** the dashboard needs to show a score's evolution over time.

## 2026-09-29 — One quadrant threshold for the dashboard: 7

- **What:** `pipeline/config.py::STRONG_THRESHOLD = 7` and `quadrant()`,
  used by both the overview counts and the detail panel.
- **Why:** the overview used 8 (and < 7 for "needs capability") while the
  detail panel used 7, so an opportunity could be "strong" in one place and
  not the other, and a right-to-win in [7, 8) matched no quadrant.
- **Changes the numbers:** on the current radar.db (133 opportunity
  spaces), overview counts go from strong/needs capability/moderate/low =
  5/1/66/61 to 12/4/87/30. The detail panel is unchanged.
- **Revisit if:** the team wants a stricter "strong" definition -- change
  the one constant.

## 2026-09-29 — Groq key rotation reads keys 1 to 5

- **What:** `GROQ_KEYS` in `llm/llm_client.py` also reads `GROQ_API_KEY_4`
  and `GROQ_API_KEY_5`.
- **Why:** `.env` defined them but the client only read keys 1-3, so a run
  fell back to slower providers while two keys were still unused.
- **Revisit if:** the provider's terms rule out rotating free-tier keys.

## 2026-09-29 — Repo housekeeping

- **What:** `tests/analyze_healthcare.py`, `tests/check_healthcare.py` and
  `tests/healthcare.txt` moved to `scripts/` (they are analysis scripts, not
  tests). Removed `docs/healthcare.txt` (byte-identical copy) and the root
  `README_6questions.md` (strict subset of `docs/README_6questions.md`).
- **Kept on purpose:** `radar_cli_top_15.py` and its summary output.
- **Revisit if:** never -- informational.

## 2026-09-29 — Summary count, one name per source, relink, rescore

- **What:** `radar_cli.py summary --top N` no longer reports the OS left out
  by the cut as "not yet scored", and rounds the sub-scores it prints.
  Google News results for TED and EUR-Lex are stored under one source name
  each (`ingest.TED_SOURCE_NAME`, `ingest.EURLEX_SOURCE_NAME`); in radar.db,
  77 `ted.europa.eu` and 251 `consultaelectoral.onpe.gob.pe` rows (EUR-Lex
  documents mirrored on another domain) were renamed. `radar_cli.py link`
  now removes existing links to `NON_TECH_SOURCES` before linking, and six
  sports/local outlets were added to that set. Then `link`,
  `scoring --force --from=OS128 --to=OS135` (Groq) and `scoring --refresh`
  were re-run.
- **Why:** the summary claimed 118 of 133 OS were unscored when all were;
  TED and EUR-Lex each counted as two sources in `source_diversity`;
  OS128-OS135 had been scored with no linked signal; the non-tech filter
  only applied to new links.
- **Changes the numbers:** links 2,899 -> 3,319, 0 OS without signals
  (was 8), 82 attractiveness totals moved (max 7.74 points), quadrants
  strong/needs capability/moderate/low = 12/4/87/30 -> 17/6/81/29.
- **Still open:** 12 links (6 articles from general outlets such as The
  Hindu or Yahoo News) are off-topic keyword matches ("Natasha Cloud" ->
  Cloud, "energy sector" strikes -> Energy). Requiring at least 2 shared
  keywords in `link` would remove most of them but changes every link --
  team decision.
- **Removed:** `docs/taxonomy_extensions.json` (unused copy),
  `docs/extend_taxonomy.md` (outdated design note),
  `opportunity_spaces_summary_top_15.md` (stale 27/08 output).

## 2026-09-30 — Power BI reads a CSV export, saved as a PBIP project

- **What:** `scripts/export_powerbi.py` writes `radar.db` to six CSV files in
  `app/powerbi_data/` (gitignored); the report is now the
  `app/innovation_radar_dashboard.pbip` project (`.Report/` and
  `.SemanticModel/` folders). The `.pbix` is kept and committed as a backup.
- **Why:** Power BI has no SQLite connector, and the old `.pbix` showed stale
  figures. A PBIP stores measures and visuals as text, so they can be diffed.
- **Revisit if:** the report has to refresh on another machine: the
  `DataFolder` parameter in `model.bim` is an absolute local path, and the
  quadrant threshold 7 is restated there instead of read from
  `STRONG_THRESHOLD`.

## 2026-09-30 — Right-to-win rescored; both summaries regenerated

- **What:** a scoring run on 30/09 (right_to_win `computed_at` 07:28-11:40
  UTC; the exact command was not recorded) replaced the right-to-win and LLM
  sub-scores. `opportunity_spaces_summary.md` is back to the full list
  (`radar_cli.py summary`) and `opportunity_spaces_summary_top_15.md` is
  regenerated with `summary --top 15 --output ...`; the top-15 file is kept
  again, which supersedes its removal in the 29/09 entry.
- **Changes the numbers:** with the same linked signals for all 133 OS, 38 OS
  changed portfolio level (15 L1->L3, 15 L3->L1, 4 L0->L1, 2 L1->L0,
  1 L3->L4, 1 L2->L3); quadrants strong/needs capability/moderate/low =
  17/6/81/29 -> 16/5/77/35; top OS by attractiveness OS097 -> OS020. The top
  15 kept the same members.
- **Why it matters:** right-to-win is not stable across LLM runs at
  temperature 0. A ranking shown to a client should be read with that margin.
- **Revisit if:** right-to-win is made reproducible (e.g. several runs and a
  majority level, or a deterministic rule on matched assets).

## 2026-09-30 — Summary urgency wording

- **What:** the urgency line in `radar_cli.py summary` no longer says "+2 per
  regulation/buying_signal signal"; it now describes what
  `scoring._urgency_weighted()` computes (regulation in full, tenders fading
  with age, a novelty term, scaled to the 95th percentile). Pinned by
  `test_urgency_line_does_not_claim_a_flat_per_signal_bonus`.
- **Why:** the old sentence described a formula the code never used. The
  urgency values themselves do not change.
  The same wrong sentence was also fixed in `radar_cli_top_15.py` and in the
  Urgency tooltip of `app/streamlit_app.py`.
- **Revisit if:** `_urgency_weighted()` changes.

## 2026-09-30 — Power BI export hardened and tested

- **What:** `scripts/export_powerbi.py` opens `radar.db` read-only, declares
  `published_on` in the header even when no signal is exported, reports how
  many present dates could not be parsed, and warns (with the value to use)
  when the model's `DataFolder` is not this repo's `app/powerbi_data/`.
  Covered by `tests/test_export_powerbi.py`.
- **Why:** the absolute `DataFolder` cannot be made relative in Power Query,
  so a fresh clone would refresh from a missing folder with no explanation;
  unparseable dates were exported as empty without a trace. Exported CSVs are
  byte-identical to the previous export on the current DB.
- **Revisit if:** Power BI gains relative paths, or the report moves to a
  shared data source.

## 2026-10-10 — Foreign keys enforced; child tables rebuilt onto the live parent

- **What:** `get_connection()` runs `PRAGMA foreign_keys = ON`, and `init_db()`
  calls `repair_foreign_key_targets()`, which rebuilds any child table whose
  FOREIGN KEY clause names a table that no longer exists. `radar.db` was
  repaired with it and committed.
- **Why:** the three child tables (`opportunity_signals`, `scores`,
  `right_to_win_scores`) referenced `"opportunity_spaces_old"`, a table a past
  migration renamed and then dropped — SQLite rewrites references on RENAME. All
  3,585 rows therefore counted as violations while every id matched the live
  table (0 orphans). With foreign keys off nothing noticed; with them on, every
  insert into those tables would have failed with "no such table".
- **What changes in the numbers:** nothing. Rows are copied one to one; the
  repair is idempotent and a healthy database is left alone.
- **Revisit if:** a future migration renames a parent table — set
  `PRAGMA legacy_alter_table = ON` around the rename, or run the repair after.
