"""Minimal T-002 FastAPI application substrate."""

from fastapi import FastAPI

from protocol.generated.python.contracts import PROTOCOL_VERSION

app = FastAPI(
    title="Continuous Authentication Backend",
    version=PROTOCOL_VERSION,
    docs_url=None,
    redoc_url=None,
)


@app.get("/healthz", include_in_schema=False)
def healthz() -> dict[str, str]:
    """Return process liveness only; protection health belongs to C7 `/v1/health`."""

    return {"status": "alive", "protocol_version": PROTOCOL_VERSION}
