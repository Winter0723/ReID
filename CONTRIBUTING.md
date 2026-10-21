# Contributing

Use a GitHub noreply commit email. Contributions to original project code are
accepted under AGPL-3.0-only; preserve third-party notices and disclose the origin
and license of copied code or assets. Do not commit model weights or personal data.

1. Start from the current source and copy `.env.sqlite.example` to `.env.local`
   for host development. See [configuration](docs/CONFIGURATION_REFERENCE.md).
2. Install the locked backend dependencies and `npm ci` in each frontend.
3. Run `python -m pytest FastAPIBackend/tests`, both frontend unit tests/builds,
   and relevant browser tests. Tests force a fresh isolated SQLite runtime and
   never consume the databases/storage in your `.env` files.
4. Run `python scripts/check_public_config.py` and
   `python scripts/export_public_source.py --check` after staging new files.
5. Describe behavior, migration requirements and validation in your pull request.

Use synthetic media in tests. Security issues belong in private reporting,
not public issues. A real payment integration must meet the documented
cryptographic and merchant consistency tests before enabling payment traffic.
