import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.db import get_connection

conn = get_connection()
rows = conn.execute(
    "\n    SELECT os.id, os.label, os.vertical,\n           s.total_score, r.right_to_win_score, r.portfolio_distance\n    FROM opportunity_spaces os\n    LEFT JOIN scores s ON s.id = (\n        SELECT id FROM scores WHERE opportunity_space_id = os.id\n        ORDER BY computed_at DESC, id DESC LIMIT 1\n    )\n    LEFT JOIN right_to_win_scores r ON r.id = (\n        SELECT id FROM right_to_win_scores WHERE opportunity_space_id = os.id\n        ORDER BY computed_at DESC, id DESC LIMIT 1\n    )\n    WHERE os.vertical = 'Healthcare'\n"
).fetchall()
for row in rows:
    print(dict(row))
conn.close()