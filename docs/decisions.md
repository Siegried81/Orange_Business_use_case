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

