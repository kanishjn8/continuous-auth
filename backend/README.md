# Backend skeleton

The backend is a Python 3.11+ package. Its T-002 executable surface currently exposes
only non-sensitive component metadata and a health probe; ingestion, persistence, risk,
and production API behavior remain their separately owned tasks.

```powershell
python -m pip install -e ".[backend,dev]"
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8765
python -m pytest backend/tests
```

The service binds to loopback in the documented command. API authentication is defined
by C7 and will be implemented in T-016 before any administrative or history surface is
made available.



