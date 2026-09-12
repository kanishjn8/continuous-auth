import sqlite3, os, csv, sys

db_path = os.environ["CA_STORAGE_DB"]
conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
conn.row_factory = sqlite3.Row

session_id = sys.argv[1]
label = sys.argv[2]
os.makedirs(f"export_{label}", exist_ok=True)

# get session time bounds for the alerts time-range filter
sess = conn.execute("SELECT started_at_utc, ended_at_utc FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
if sess is None:
    print(f"WARNING: session_id {session_id} not found in sessions table")
    start_ts, end_ts = None, None
else:
    start_ts = sess["started_at_utc"]
    end_ts = sess["ended_at_utc"]  # may be None if session wasn't closed cleanly

tables_and_queries = {
    "feature_windows": ("SELECT * FROM feature_windows WHERE session_id = ?", (session_id,)),
    "scores": ("""SELECT s.* FROM scores s
                  JOIN feature_windows f ON s.window_id = f.window_id
                  WHERE f.session_id = ?""", (session_id,)),
    "risk_events": ("SELECT * FROM risk_events WHERE session_id = ?", (session_id,)),
    "decisions": ("""SELECT d.* FROM decisions d
                      JOIN risk_events r ON d.decision_id = r.decision_id
                      WHERE r.session_id = ?""", (session_id,)),
    "verification_anchors": ("SELECT * FROM verification_anchors WHERE session_id = ?", (session_id,)),
}

if end_ts:
    tables_and_queries["alerts"] = (
        "SELECT * FROM alerts WHERE occurred_at_utc >= ? AND occurred_at_utc <= ? ORDER BY occurred_at_utc",
        (start_ts, end_ts)
    )
else:
    # session never got an ended_at_utc (e.g. you Ctrl+C'd without clean shutdown) - use everything from start onward
    tables_and_queries["alerts"] = (
        "SELECT * FROM alerts WHERE occurred_at_utc >= ? ORDER BY occurred_at_utc",
        (start_ts,)
    )
    print(f"NOTE: session {session_id} has no ended_at_utc - alerts export is start_ts onward, unbounded. "
          f"You may pick up alerts from a LATER session too if one started right after. Check the timestamps in the CSV.")

for table, (query, params) in tables_and_queries.items():
    try:
        rows = conn.execute(query, params).fetchall()
    except sqlite3.OperationalError as e:
        print(f"[{table}] query failed: {e}")
        continue
    if not rows:
        print(f"[{table}] 0 rows")
        continue
    with open(f"export_{label}/{table}.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(rows[0].keys())
        for r in rows:
            writer.writerow(list(r))
    print(f"[{table}] {len(rows)} rows -> export_{label}/{table}.csv")
