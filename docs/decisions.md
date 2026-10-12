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

## 2026-10-12 — Score history kept next to the current score

- **What:** two append-only tables, `score_history` and `right_to_win_history`
  (`HISTORY_SCHEMA`, `pipeline/db.py`). `insert_score()` and
  `insert_right_to_win_score()` still update one current row per opportunity
  space, and now also append a copy of each accepted write, in the same
  transaction. The tables are created on the first score write (or by
  `init_db()`) and seeded once with the current rows, marked `baseline`.
- **Relation to 2026-09-29:** that entry stands for `scores` and
  `right_to_win_scores` — still one row each, still what every read uses. It
  rejected history because nothing read it; the right-to-win instability
  (§8 of the deep dive: 38 of 133 OS moved level between two runs) is now a
  question that needs it, and an extra table answers it without touching the
  four places that assume one row.
- **What changes in the numbers:** nothing. No score, quadrant, summary or
  export reads the history; latest-score reads on a copy of the committed
  `radar.db` are identical before and after the tables are created
  (`tests/test_score_history.py`). The committed `radar.db` is not modified in
  this change; it gains the tables on its next scoring run.
- **No foreign key on the history:** with foreign keys on, a reference would
  make every delete of an opportunity space fail, and a history should
  outlive the space anyway. Label and triple are stored with each row because
  `wipe` resets the id sequence.
- **Revisit if:** the history grows large enough to matter (one row per OS per
  scoring run), or the dashboard starts showing a score's evolution.

## 2026-10-12 — One taxonomy proposal threshold: 5

- **What:** `pipeline/config.py::TAXONOMY_PROPOSAL_THRESHOLD = 5`, read by the
  defaults of `extend_taxonomy.get_terms_reaching_threshold()` and
  `generate_all_proposals()` and by the call in `run_extend_taxonomy()`.
- **Why:** `generate_all_proposals()` defaulted to 3 while the pipeline called
  it with 5 (and `get_terms_reaching_threshold()` defaulted to 5): a caller
  relying on the default would have proposed 33 terms from the committed
  `radar.db` instead of 8.
- **What changes in the numbers:** nothing. 5 is the value the pipeline's only
  call already used; no code path called the function with its old default.
  Only a future call without a threshold now gets 5 instead of 3.
- **Revisit if:** the threshold is calibrated, which is a team decision; it is
  now one line to change.

## 2026-10-12 — `link` requires 2 shared keywords

- **What:** `radar_cli.py::MIN_KEYWORD_OVERLAP = 2`. `link` only links a signal
  whose title shares at least 2 distinct keywords with the OS's use case +
  technology, and first removes existing links below that minimum (for the OS
  they are weak for only, as it already did for `NON_TECH_SOURCES`).
  `calibrate` counts matching signals with the same minimum.
- **Why:** one shared word linked "Natasha Cloud" (a basketball player) to two
  Cloud OS. Two rules were measured against a seeded sample of 120 committed
  links, hand-labelled (`docs/link_sample_2026-10-12.csv`: 40 per stratum,
  `random.Random(20261012)`; on-topic = a reader of that OS would accept the
  article as evidence about its technology or its use case; off-topic = the
  word in another sense, or unrelated beyond the shared word):

  | Stratum (committed links) | Links | Off-topic in sample |
  |---|---|---|
  | 1 keyword, signal vertical = OS vertical | 1,698 | 13 / 40 |
  | 1 keyword, other | 1,152 | 18 / 40 |
  | 2+ keywords | 469 | 3 / 40 |

  "2 keywords" keeps only the last stratum; "1 keyword + same vertical" also
  keeps the first, with a third of it off-topic and the Natasha Cloud links
  in it (their signals carry the OS's vertical). The 2-keyword rule leaves
  fewer off-topic links, so it was chosen.
- **Changes the numbers:** re-linking a copy of the committed database, links
  3,319 -> 469 (the alternative rule: 2,223). Extrapolated from the sample,
  about 1,070 off-topic links disappear and about 1,780 legitimate ones are
  lost. 32 of 133 OS end with no link at all, OS020 (top attractiveness, 9.19)
  among them; the median OS keeps 2 signals and the widest 37, so none
  reaches `MARKET_SIGNAL_CAP` 56 and market signal strength tops out at 6.6.
  Re-scoring will drop the attractiveness of every OS that lost signals.
  `docs/calibrate_output.txt` is regenerated on the relinked copy. The
  committed `radar.db` keeps the old links until `link` and scoring are re-run.
- **Revisit if:** the 32 OS without evidence matter more than the off-topic
  links -- most of them repeat one generic word in use case and technology
  ("Cloud Infrastructure Modernization x Cloud"), so a minimum relative to the
  OS's own keyword count, or matching on the signal summary too, would recover
  them. `MARKET_SIGNAL_CAP` (56) was set for the old link counts and should be
  re-derived from the new ones.

## 2026-10-12 — Attractiveness weights from the AHP sheet

- **What:** `pipeline/ahp.py` reads the upper triangle of
  `pipeline/ahp_weights.xlsx`, rebuilds the reciprocal matrix, and computes the
  priority vector (normalised row geometric means, the sheet's own method) and
  the consistency ratio (Saaty RI, 1.12 for 5 criteria); it refuses judgments
  with CR >= 0.10. `pipeline/scoring.py` has two named weightings, `ahp` (the
  default, `WEIGHTING`) and `manual` (the hand-set 30/20/25/10/15), and
  `python -m pipeline.scoring --weights=manual` selects the latter for a run.
  The sheet moved from `tests/` to `pipeline/` because the Docker image, which
  runs the pipeline, excludes `tests/`; `openpyxl==3.1.5` joins
  `requirements.txt` to read it.
- **Why:** the weights were a hand choice; the sheet held the team's pairwise
  judgments but nothing read it.
- **Changes the numbers:** weights market/evidence/diversity/relevance/novelty
  go from 30/25/20/15/10 to 39.7/27.8/14.1/13.0/5.5 (CR 0.021; the principal
  eigenvector agrees within 0.2 points). Re-totalled from the stored
  sub-scores of all 133 OS on the committed database: the top 15 keep the same
  members, with 4 adjacent swaps (OS013 5th / OS001 4th, OS134 11th -> 9th,
  OS029 9th -> 10th, OS017 12th -> 11th, OS014 10th -> 12th); 103 OS change
  rank, by at most 11 places; totals move by at most 0.42; quadrants
  strong/needs capability/moderate/low 16/5/77/35 -> 19/5/74/35 (OS034, OS065,
  OS108 become strong; OS073 low -> needs capability; OS002 needs capability
  -> low). The committed `radar.db` keeps the manual-weight totals until
  scoring is re-run; `scoring --refresh` re-totals without calling the LLM.
- **Revisit if:** outcome data (won/lost opportunities) becomes available --
  AHP makes judgments consistent, it does not validate them. Changing a
  judgment means editing the yellow cells of the sheet; the code recomputes
  and re-checks CR on the next run.

## 2026-10-12 — Right-to-win reference bonus stays hand-set, now a named parameter

- **What:** the bonus is `CUSTOMER_REFERENCE_BONUS = 0.5` per named customer
  reference in the vertical, capped at `CUSTOMER_REFERENCE_BONUS_CAP = 1.0`,
  in `pipeline/scoring.py`; `customer_reference_bonus()` replaces
  `crm_customer_overlap_bonus()`. `scripts/rtw_bonus_sensitivity.py` re-applies
  other values to the stored scores and counts what moves, without the LLM.
- **Why not fitted:** fitting needs an outcome per OS (won, lost, pursued),
  and the radar has none: the two CRM dicts that would have held it were
  empty and are gone, and the 18 customer references are the input of the
  bonus, not an outcome. Any "fitted" value would be invented, so the value
  is kept and its sensitivity published instead.
- **Changes the numbers:** none at the default. Sensitivity on the committed
  database (133 OS, 77 with a bonus): at 0.25 per reference 1 OS changes
  quadrant (OS107), at 0.75 none, with no bonus 7 (all L3 OS scored 6.5 by
  the LLM; strong 16 -> 14). The portfolio level never moves. The
  justification now names the reference count instead of "CRM customer
  overlap", which described data the code never read; stored justifications
  keep the old wording until right-to-win is re-scored.
- **Revisit if:** won/lost outcomes per OS become available -- then fit the
  bonus (or drop it: the references are already in the prompt, so the LLM may
  count them once already).
