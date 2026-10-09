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
- Upload a data file without executing it.
- Store an append-style receipt for each workspace action and upload, with status, timestamps, a safe summary, and count-only verification facts.

No shell, arbitrary Python, browser automation, email sending, financial transaction, or other unrestricted tool is available.

## Process boundaries

- A workspace root is created under the user's home directory by default.
- File paths are resolved beneath that root; traversal and symlink paths are rejected.
- Uploaded files are stored, never executed.
- Upload, input, CSV-row, duplicate-scan, and model request sizes are bounded.
- The web server is intended to bind to loopback for a single local user.
- Optional model classification talks to a loopback Ollama endpoint only. If it is unavailable or returns an invalid schema, deterministic routing is used.

## Action receipts and restart recovery

Receipts are stored in a local SQLite database. By default the database is beside the workspace at `~/.some/receipts.sqlite3`; `SOME_RECEIPTS_DB` can select another path. Receipts persist action type, UTC timestamps, status, a short summary, count-based verification facts, and whether an interrupted action's recovery warning has been acknowledged. They do not store raw user prompts, note text, CSV cell values, or file paths.

- `GET /api/receipts?limit=50&offset=0` lists recent receipts (maximum 100 per page).
- `GET /api/receipts/{id}` retrieves one receipt.
- `POST /api/receipts/{id}/acknowledge-recovery` records acknowledgement of an interrupted operation after the user has inspected the workspace.
- A startup recovery sweep marks leftover `running` records as `interrupted`. SOME never automatically retries interrupted work because a crash can occur after a side effect and before its receipt is completed.
- File organization returns a fresh receipt after the moves and checks that source files are absent and each destination exists. If the check is inconclusive, the receipt remains marked interrupted.

The Activity control in the UI displays the local history and the recovery warning. Acknowledgement only records that the user reviewed the warning; it does not certify that all files are correct.

## Known limits

This repository is still an early single-user MVP, not a multi-tenant service. Confirmation plans and rate limits remain in memory and are not distributed controls. Receipt storage improves observability and recovery but does not make a multi-file filesystem operation perfectly atomic across power loss. Before hosted use, independently review identity, per-tenant storage isolation, durable jobs, secrets handling, observability without sensitive payloads, abuse controls, backup/deletion processes, and deployment-level TLS. Add external integrations only with explicit user authorization and scoped credentials.
