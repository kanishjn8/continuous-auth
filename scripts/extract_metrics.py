import csv, statistics, sys
from datetime import datetime
from collections import Counter

def load(path):
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []

def pct(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    f, c = int(k), min(int(k) + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)

def score_stats(rows, field):
    vals = sorted(float(r[field]) for r in rows if r.get(field) not in (None, ""))
    if not vals:
        return None
    return {
        "n": len(vals), "min": vals[0], "max": vals[-1],
        "mean": statistics.mean(vals), "median": statistics.median(vals),
        "stdev": statistics.stdev(vals) if len(vals) > 1 else 0.0,
        "p05": pct(vals, 0.05), "p25": pct(vals, 0.25),
        "p75": pct(vals, 0.75), "p95": pct(vals, 0.95),
    }

def print_stats(label, s):
    if s is None:
        print(f"  {label}: no data")
        return
    print(f"  {label}: n={s['n']}  mean={s['mean']:.4f}  median={s['median']:.4f}  "
          f"stdev={s['stdev']:.4f}  min={s['min']:.4f}  p05={s['p05']:.4f}  "
          f"p25={s['p25']:.4f}  p75={s['p75']:.4f}  p95={s['p95']:.4f}  max={s['max']:.4f}")

def parse_ts(ts):
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(ts, fmt)
        except (ValueError, TypeError):
            continue
    return None

BASELINE_DIR = "export_baseline"
DRILL_DIR = "export_drill"

drill_events = load(f"{DRILL_DIR}/risk_events.csv")
baseline_events = load(f"{BASELINE_DIR}/risk_events.csv")
decisions = load(f"{DRILL_DIR}/decisions.csv")
alerts = load(f"{DRILL_DIR}/alerts.csv")
anchors = load(f"{DRILL_DIR}/verification_anchors.csv")

print("=" * 60); print("A. DATASET METRICS"); print("=" * 60)
print(f"Genuine baseline windows (this session): {len(baseline_events)}")
print(f"Attacker (drill) windows: {len(drill_events)}")

print(); print("=" * 60); print("F. TIMING / LATENCY"); print("=" * 60)
sorted_drill = sorted(drill_events, key=lambda x: x.get("stored_at_utc",""))

T_ATTACK_START = "2026-09-12T10:25:00Z"
t0 = parse_ts(T_ATTACK_START)
print(f"  t0 (attack start): {T_ATTACK_START}")

def first_time_for(val, field, rows):
    for r in sorted(rows, key=lambda x: x.get("stored_at_utc") or ""):
        if r.get(field) == val:
            ts = parse_ts(r.get("stored_at_utc"))
            if ts: return ts, r.get("stored_at_utc")
    return None, None

if t0:
    for name, val, field, rows in [
        ("first MEDIUM", "MEDIUM", "risk_level", drill_events),
        ("first HIGH", "HIGH", "risk_level", drill_events),
        ("first REAUTH requested", "REAUTH", "action", drill_events),
        ("first TERMINATE", "TERMINATE", "action", drill_events),
    ]:
        t, raw = first_time_for(val, field, rows)
        if t:
            print(f"  {name}: {raw}  ->  {(t - t0).total_seconds():.1f} s from t0")
        else:
            print(f"  {name}: not reached")
