# Legacy archive

This directory contains MyReID surfaces that are preserved only for historical
reference. They are not imported by the backend, built by CI, included in
production images, or supported for new deployments.

## Freeze policy

- Frozen on: 2026-09-01
- Last compatibility version: `0.1.0`
- Last pre-archive source baseline: commit `909a7d0`
- Supported desktop client: [`../TauriFrontend/`](../TauriFrontend/)
- Supported HTTP surface: `/api/v1`
- New features, compatibility work, security fixes, release builds, and
  production support: stopped

## Contents

- [`qt-qml-client/`](qt-qml-client/): the former Qt/QML desktop client and its
  historical launcher/contract smoke script.
- [`api/`](api/): the pre-v1 compatibility API implementation.

`ENABLE_LEGACY_API=1` is intentionally rejected during configuration. To
inspect the exact source before the move, use `git show 909a7d0:QtFrontend/...`
or `git show 909a7d0:FastAPIBackend/app/api/legacy.py`.
