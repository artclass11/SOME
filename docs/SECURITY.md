# Security

## Safe defaults

- Local workspace; no analytics or telemetry.
- The model integration is optional and local-only by design.
- Requests are translated into a fixed action vocabulary. Model text is never executed as code.
- File movement requires preview plus a second confirmation.
- File paths are contained inside the workspace and symlinks are rejected.
- Upload size, message length, CSV rows, and scan work are capped.
- Uploaded files are treated as data and are never executed.
- Frontend JavaScript is served as a same-origin static asset; the Content Security Policy disallows inline scripts.
- File organization checks all proposed paths before moving files, avoids overwriting destinations, and attempts rollback after a later move fails.

## Important deployment warning

This MVP has no user authentication or multi-user authorization. The local server must remain bound to 127.0.0.1. Do not expose it on a LAN or internet, port-forward it, or deploy it as a public hosted service. A loopback bind is not a substitute for authentication, sandboxing, or operating-system protections.

Before any remote deployment, require a security design and review for user identity, strong authorization, tenant isolation, CSRF/origin policy, TLS, secure session management, secrets, audit trails, resource quotas, persistent queue hardening, abuse prevention, dependency scanning, backups, data deletion, and incident response. The current origin and rate checks are defense-in-depth for a single-user local application, not remote authentication or multi-tenant security. Re-review the threat model whenever new actions or integrations are introduced.

## Data handling

Workspace files remain on the machine running SOME. If a user separately configures a local Ollama model, chat instructions are sent to that local process for classification. Do not configure a remote or shared inference endpoint. Never put credentials, private documents, or user data in issue reports.

## Reporting a vulnerability

For a vulnerability that could affect users, avoid posting exploit details publicly. Contact the repository owner privately through GitHub's security reporting mechanism if it is enabled, or ask for a private reporting channel. Include affected versions, impact, a minimal reproduction, and a suggested mitigation. Do not attach real user data or secrets.
