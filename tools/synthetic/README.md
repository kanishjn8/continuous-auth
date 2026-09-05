# T-003 synthetic event and score generator

This tool creates deterministic, content-free C1 event frames, expected C2
feature windows, risk-oriented C3 score fixtures, and expected risk labels.
It also creates isolated sequence-gap, duplicate, out-of-order, malformed,
and oversized-frame streams for collector/ingestion tests.

All generated material is labelled `SYNTHETIC`. It is for development and
pipeline mechanics only and must never be used for headline evaluation.
No typed content, platform key identifier, title, path, URL, or screen data
exists in its configuration or output.

From the repository root, validate a scenario without writing data:

```powershell
python -m tools.synthetic --scenario baseline --check
python -m tools.synthetic --scenario keyboard_sparse --check
python -m tools.synthetic --scenario takeover --check
```

To create local ignored fixtures for manual inspection:

```powershell
python -m tools.synthetic --scenario takeover --output data/synthetic/takeover
```

The framing fixture is portable and transport-independent: a four-byte
unsigned network-order length followed by canonical UTF-8 JSON. T-005 carries
these frames over a Windows named pipe or a private macOS Unix socket;
T-007 consumes them incrementally.

Edit only `config/synthetic.development.yaml` to tune generator behavior.
The loader rejects extra fields, invalid probabilities, invalid ranges,
unknown profiles, phase gaps, and missing values. Sampling uses rejection;
it raises an error rather than silently clipping an impossible distribution.
