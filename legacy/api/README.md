# Pre-v1 API archive

`pre_v1.py` is the frozen implementation of the HTTP routes that predated
`/api/v1`. It is retained for source-history reference only and is not imported
or mounted by the FastAPI application.

- Last compatibility version: `0.1.0`
- Last pre-archive source baseline: `909a7d0`
- Freeze date: 2026-09-01
- Replacement: the versioned `/api/v1` routes documented in
  [`../../docs/API.md`](../../docs/API.md)

Setting `ENABLE_LEGACY_API=1` fails configuration deliberately. Do not copy
this module back into the active backend or use it for a production deployment.
