# T-009 local storage

This package persists only validated aggregate contracts. It does not accept raw input
events, typed content, key identifiers, application titles, document names, clipboard
material, images, or browsing addresses.

## What it provides

- the twelve tables required by `PLAN.md` section 7.4;
- forward-only, checksummed database migrations;
- SQLite WAL mode, foreign keys, integrity checks, indexes, and atomic transactions;
- a transactional audit outbox and append-only, hash-chained daily JSONL logs;
- configured compression/deletion and feature/score retention;
- a process-name-only application registry;
- schema/config versions on stored records;
- access-restricted host-local directories; and
- loud availability events on permission, migration, integrity, or write failure.

An availability failure is deliberately not an enforcement instruction. Callers must
enter their safe degraded behavior, keep the legitimate session usable, and display the
availability alert distinctly from behavioral risk.

## Migration policy

Migrations are forward-only. Applied migration checksums are recorded. Never edit an
applied SQL migration; add the next numbered migration. A failed migration is rolled
back. Recovery is: preserve the failed database for diagnosis, correct the new migration
before it has been accepted anywhere, and restart. A checksum mismatch requires restoring
the original migration rather than changing history.

## Configuration policy

`config/storage.development.yaml` can store synthetic fixtures only. The service checks
that boundary before it writes a feature window or derived score/decision/model. It is
not an approved pilot or evaluation configuration.

Pilot/evaluation retention remains a human-approved privacy decision. A separate C9-valid
configuration must be supplied before real collection; raw debug capture must remain off
during pilot collection.


