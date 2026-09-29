from datetime import datetime
import json
import os
from pipeline.taxonomy_validation import is_generic_taxonomy_term
from pipeline.db import get_connection

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TAXONOMY_EXTENSIONS_PATH = os.path.join(BASE_DIR, "taxonomy_extensions.json")


def _add_to_extensions(term, category):
    with open(TAXONOMY_EXTENSIONS_PATH, "r") as f:
        data = json.load(f)
    if not any(
        (item["term"] == term and item["category"] == category for item in data)
    ):
        data.append({"term": term, "category": category})
    with open(TAXONOMY_EXTENSIONS_PATH, "w") as f:
        json.dump(data, f, indent=2)


def init_proposals_table(conn):
    conn.execute(
        "\n        CREATE TABLE IF NOT EXISTS proposals (\n            id INTEGER PRIMARY KEY AUTOINCREMENT,\n            vertical TEXT NOT NULL,\n            proposed_use_case TEXT,\n            proposed_technology TEXT,\n            frequency INTEGER NOT NULL,\n            first_seen TEXT NOT NULL,\n            last_seen TEXT NOT NULL,\n            status TEXT DEFAULT 'pending',\n            reviewed_at TEXT,\n            reviewed_by TEXT,\n            run_id TEXT,\n            UNIQUE(vertical, proposed_use_case, proposed_technology)\n        )\n    "
    )
    conn.commit()


def get_terms_reaching_threshold(conn, threshold=5):
    rows = conn.execute(
        "SELECT vertical, term, category, frequency, first_seen, last_seen\n           FROM watchlist_terms\n           WHERE frequency >= ?\n           ORDER BY frequency DESC",
        (threshold,),
    ).fetchall()
    return rows


def proposal_exists(conn, vertical, use_case, technology):
    row = conn.execute(
        "SELECT id, status FROM proposals \n           WHERE vertical = ? \n           AND (proposed_use_case = ? OR (proposed_use_case IS NULL AND ? IS NULL))\n           AND (proposed_technology = ? OR (proposed_technology IS NULL AND ? IS NULL))",
        (vertical, use_case, use_case, technology, technology),
    ).fetchone()
    return row


def generate_proposal(conn, term, run_id=None):
    vertical = term["vertical"]
    category = term["category"]
    term_value = term["term"]
    frequency = term["frequency"]
    first_seen = term["first_seen"]
    last_seen = term["last_seen"]
    proposed_use_case = term_value if category == "use_case" else None
    proposed_technology = term_value if category == "technology" else None
    if is_generic_taxonomy_term(term_value, category):
        return "skipped"
    existing = proposal_exists(conn, vertical, proposed_use_case, proposed_technology)
    if existing:
        return "exists"
    conn.execute(
        "INSERT INTO proposals \n        (vertical, proposed_use_case, proposed_technology, \n        frequency, first_seen, last_seen, status, run_id)\n        VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
        (
            vertical,
            proposed_use_case,
            proposed_technology,
            frequency,
            first_seen,
            last_seen,
            run_id,
        ),
    )
    conn.commit()
    return "inserted"


def generate_all_proposals(conn, threshold=3, run_id=None):
    terms = get_terms_reaching_threshold(conn, threshold)
    inserted = 0
    exists = 0
    for term in terms:
        result = generate_proposal(conn, term, run_id)
        if result == "inserted":
            inserted += 1
        elif result == "exists":
            exists += 1
    return (inserted, exists)


def approve_proposal(conn, proposal_id, reviewed_by="team"):
    proposal = conn.execute(
        "SELECT * FROM proposals WHERE id = ?", (proposal_id,)
    ).fetchone()
    if not proposal:
        return False
    term = proposal["proposed_use_case"] or proposal["proposed_technology"]
    category = "use_case" if proposal["proposed_use_case"] else "technology"
    if is_generic_taxonomy_term(term, category):
        print(
            f"[!] Proposal {proposal_id} was not approved: {term!r} is too generic for the taxonomy."
        )
        return False
    _add_to_extensions(term, category)
    conn.execute(
        "UPDATE proposals \n           SET status = 'approved', reviewed_at = ?, reviewed_by = ?\n           WHERE id = ?",
        (datetime.now().isoformat(), reviewed_by, proposal_id),
    )
    conn.commit()
    if proposal["proposed_use_case"]:
        conn.execute(
            "DELETE FROM watchlist_terms WHERE term = ? AND category = 'use_case' AND vertical = ?",
            (proposal["proposed_use_case"], proposal["vertical"]),
        )
    elif proposal["proposed_technology"]:
        conn.execute(
            "DELETE FROM watchlist_terms WHERE term = ? AND category = 'technology' AND vertical = ?",
            (proposal["proposed_technology"], proposal["vertical"]),
        )
    conn.commit()
    print(f"[+] Proposal {proposal_id} approved and taxonomy updated automatically.")
    return True


def reject_proposal(conn, proposal_id, reviewed_by="team"):
    conn.execute(
        "UPDATE proposals \n           SET status = 'rejected', reviewed_at = ?, reviewed_by = ?\n           WHERE id = ?",
        (datetime.now().isoformat(), reviewed_by, proposal_id),
    )
    conn.commit()
    print(f"[-] Proposal {proposal_id} rejected.")
    return True


def get_proposals(conn, status=None):
    if status:
        query = "SELECT * FROM proposals WHERE status = ? ORDER BY frequency DESC"
        params = (status,)
    else:
        query = "SELECT * FROM proposals ORDER BY frequency DESC"
        params = ()
    return conn.execute(query, params).fetchall()


def print_proposals(conn):
    pending = get_proposals(conn, "pending")
    approved = get_proposals(conn, "approved")
    rejected = get_proposals(conn, "rejected")
    print(f"{'=' * 20} Proposals Summary {'=' * 20}")
    print(f"   Pending: {len(pending)}")
    print(f"   Approved: {len(approved)}")
    print(f"   Rejected: {len(rejected)}")
    if pending:
        print("\nPending Proposals:")
        for p in pending:
            print(
                f"   {p['vertical']} × {p['proposed_use_case']} × {p['proposed_technology']} (freq: {p['frequency']})"
            )


def run_review(conn):
    pending = get_proposals(conn, status="pending")
    if not pending:
        print("No proposals pending for review.")
        return
    print(f"{len(pending)} pending proposals found.\n")
    for p in pending:
        term_desc = (
            p["proposed_use_case"]
            if p["proposed_use_case"]
            else p["proposed_technology"]
        )
        category = "use_case" if p["proposed_use_case"] else "technology"
        print(
            f"ID {p['id']}: {p['vertical']} × {term_desc} (frequency: {p['frequency']})"
        )
        print(f"  Category: {category}")
        print("  1 = Approve, 2 = Reject, 0 = Skip")
        choice = input("> ").strip()
        if choice == "1":
            approve_proposal(conn, p["id"])
        elif choice == "2":
            reject_proposal(conn, p["id"])
        elif choice == "0":
            continue
        else:
            print("Invalid choice, skipping.")


def run_extend_taxonomy(run_id=None):
    conn = get_connection()
    init_proposals_table(conn)
    inserted, exists = generate_all_proposals(conn, threshold=5, run_id=run_id)
    print("[OK] Extend taxonomy complete:")
    print(f"   New proposals: {inserted}")
    print(f"   Already exists: {exists}")
    pending = get_proposals(conn, "pending")
    if pending:
        print(f"\nPending proposals ({len(pending)}):")
        for p in pending:
            print(
                f"   {p['vertical']} × {p['proposed_use_case']} × {p['proposed_technology']} (freq: {p['frequency']})"
            )
    conn.close()


if __name__ == "__main__":
    run_extend_taxonomy()