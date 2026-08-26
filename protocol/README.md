# Protocol v1

`schemas/contracts.schema.json` is the canonical C1-C9 data-contract source.
The smaller `*.schema.json` documents are stable entry points into that catalogue, and
`schemas/api.openapi.yaml` is the C7 REST contract. The WebSocket C8 envelope and C9
configuration schema are also defined in the catalogue.

Regenerate typed consumers with:

```powershell
python protocol/codegen/generate.py
```

Verify that committed bindings are current with:

```powershell
python protocol/codegen/generate.py --check
```

Generated files must not be edited. Breaking changes require a protocol version bump,
consumer migration notes, regenerated bindings, and producer/consumer review.

## Engineering Recommendation

T-002 uses JSON Schema Draft 2020-12 plus a small repository-owned deterministic
generator. This keeps the contract source inspectable and avoids two independently
maintained language models. The generator is intentionally narrow: unsupported schema
constructs fail code generation instead of producing a weak type.


