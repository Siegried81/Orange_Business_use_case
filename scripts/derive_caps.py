"""Re-derive MARKET_SIGNAL_CAP and SOURCE_DIVERSITY_CAP for the current link rule.

Both caps scale a count to 0-10 (`min(10, n / cap * 10)`), so they only mean
something relative to how many signals and publishers `link` gives an OS. They
were set (56 and 40) for the one-keyword rule; under the two-keyword rule the
widest OS has 37 signals and 28 publishers, so no OS could approach 10 and
"strong" became out of reach.

Method: keep each cap at the same position in its distribution. The position
of an old cap is its mid-rank percentile in the per-OS distribution under the
old rule, (count below + half the count equal) / n, which stays defined when
many OS tie at the cap. The new cap is the inclusive quantile of the
distribution under the current rule at that percentile, rounded to the nearest
integer (at least 1). The 90th percentile of the new distribution is printed
next to it as a cross-check.

Both distributions are replayed from the signals with `radar_cli.matching_signals`
at minimum 1 and at MIN_KEYWORD_OVERLAP, cut at `link`'s top_n, so the result
does not depend on which links radar.db currently stores. Nothing is written.

Usage, from the repo root:
    python scripts/derive_caps.py
"""
import inspect
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import radar_cli  # noqa: E402
from pipeline.db import get_all_opportunity_spaces  # noqa: E402

DB_PATH = Path(__file__).resolve().parent.parent / "radar.db"
# The caps as they were sized for the one-keyword rule (min overlap 1). They
# are the reference point of the method, so they are fixed here rather than
# read from scoring.py, which holds the derived values.
ONE_KEYWORD_RULE = 1
OLD_CAPS = {"market_signal_strength": 56, "source_diversity": 40}


def per_os_counts(conn, min_overlap, top_n):
    """Linked signals and distinct publishers per OS under a given keyword minimum."""
    signals, publishers = [], []
    for os_row in get_all_opportunity_spaces(conn):
        kept = [s for _, s in radar_cli.matching_signals(conn, os_row, min_overlap)[:top_n]]
        signals.append(len(kept))
        publishers.append(len({s["source_name"] for s in kept}))
    return signals, publishers


def mid_rank_percentile(values, x):
    """(count below x + half the count equal to x) / n."""
    below = sum(v < x for v in values)
    equal = sum(v == x for v in values)
    return (below + 0.5 * equal) / len(values)


def quantile(values, p):
    """Inclusive quantile with linear interpolation (numpy's default, method 7)."""
    ordered = sorted(values)
    h = (len(ordered) - 1) * p
    lo = int(h)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (h - lo) * (ordered[hi] - ordered[lo])


def derive(conn, top_n=None):
    """{sub_score: {...}} with old/new distributions, the percentile and both candidate caps."""
    if top_n is None:
        top_n = inspect.signature(radar_cli.cmd_link).parameters["top_n"].default
    old = dict(zip(OLD_CAPS, per_os_counts(conn, ONE_KEYWORD_RULE, top_n)))
    new = dict(zip(OLD_CAPS, per_os_counts(conn, radar_cli.MIN_KEYWORD_OVERLAP, top_n)))
    result = {}
    for key, old_cap in OLD_CAPS.items():
        p = mid_rank_percentile(old[key], old_cap)
        exact = quantile(new[key], p)
        result[key] = {
            "old_cap": old_cap,
            "old_values": old[key],
            "new_values": new[key],
            "percentile": p,
            "old_saturated": sum(v >= old_cap for v in old[key]),
            "new_quantile": exact,
            "new_cap": max(1, round(exact)),
            "new_p90": quantile(new[key], 0.9),
            "new_max": max(new[key]),
        }
    return result


def main():
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    result = derive(conn)
    conn.close()
    print("| Sub-score | Old cap | Its percentile (old rule) | OS at/above old cap | New quantile there | New cap | New p90 | New max |")
    print("|---|---|---|---|---|---|---|---|")
    for key, r in result.items():
        print(
            f"| {key} | {r['old_cap']} | {r['percentile']:.3f} | {r['old_saturated']} of {len(r['old_values'])} | "
            f"{r['new_quantile']:.2f} | {r['new_cap']} | {r['new_p90']:.1f} | {r['new_max']} |"
        )
    for key, r in result.items():
        new = sorted(r["new_values"])
        print(f"\n{key}: new distribution min {new[0]}, median {quantile(new, 0.5):g}, max {new[-1]}; "
              f"OS at/above new cap {sum(v >= r['new_cap'] for v in new)}")


if __name__ == "__main__":
    main()
