"""Per-user modelling (T-010).

Trains independent keyboard and mouse Isolation Forest models per user
(ADR-006), with per-user preprocessing and percentile score calibration
(PLAN.md Section 10). No user's model is ever trained on another user's
data (P3) -- enforced structurally: every function here takes a single
user's windows and has no parameter through which another user's data
could enter.
"""
