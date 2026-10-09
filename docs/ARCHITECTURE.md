# Architecture

## Product shape

SOME uses a chat-like interface as the simplest input surface, but its core output is a completed operation or an inspectable plan. The runtime is deliberately small:

1. The user submits a short instruction.
2. The planner maps it to one typed action. Keyword rules work without a model; an optional local Ollama model can classify ambiguous requests.
3. The runtime validates the action and arguments against code-owned allowlists.
4. Read-only actions execute immediately. File movement returns a preview and short-lived confirmation ID.
5. A narrowly scoped tool runs inside a dedicated local workspace and returns measured output.
6. The UI renders that result as an operation receipt, not as an unverified model claim.

## Current actions

- List workspace files and their sizes.
- Find duplicate content with SHA-256 hashes.
- Inspect CSV shape and missing values.
- Clean a CSV to a new output file, normalizing headers and trimming cells while preserving the original.
- Preview file organization by extension; move only after a separate confirmation.
- Create a note from content the user supplied.

No shell, arbitrary Python, browser automation, email sending, financial transaction, or other unrestricted tool is available.

## Process boundaries

- A workspace root is created under the user's home directory by default.
- File paths are resolved beneath that root; traversal and symlink paths are rejected.
- Uploaded files are stored, never executed.
- Upload, input, CSV-row, duplicate-scan, and model request sizes are bounded.
- The web server is intended to bind to loopback for a single local user.
- Optional model classification talks to a loopback Ollama endpoint only. If it is unavailable or returns an invalid schema, deterministic routing is used.

## Known limits

This repository is an early single-user MVP, not a multi-tenant service. In-memory confirmation plans and rate limits do not survive restarts and are not distributed controls. Before hosted use, implement and independently review identity, per-tenant storage isolation, durable jobs/receipts, secrets handling, observability without sensitive payloads, abuse controls, backup/deletion processes, and deployment-level TLS. Add external integrations only with explicit user authorization and scoped credentials.
