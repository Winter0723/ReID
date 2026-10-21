# Security policy

Only the latest published source snapshot is supported. Apply security fixes
before exposing a deployment. Real WeChat payments are currently unsupported;
see [the integration blockers](docs/WECHAT_PAYMENT_INTEGRATION.md).

Report vulnerabilities through the repository's **Security → Report a
vulnerability** private reporting feature. The repository owner must enable
this feature when creating the public repository. If it is unavailable, open
an issue asking for a private reporting channel without disclosing exploit
details, credentials, photos or visitor records. No response SLA is promised.

Include the revision, affected endpoint, minimal synthetic reproduction and
expected/actual behavior. Do not test against someone else's deployment.

Deployment requirements: HTTPS, private buckets, loopback API binding behind a
trusted proxy, least-privilege accounts, verified SMTP TLS, backups and retention
maintenance. Do not publish live `.env` files, secrets, media, database dumps,
debug logs or old Git history. Rotate any credential previously captured in
OAuth/client logs after examining your deployment's logs privately.
