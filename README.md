# SOME

**Small Operations, Massive Execution.** A local-first, action-oriented AI workbench that behaves like a chat app: say what you need, review the plan, approve changes, and get a real result.

SOME is not a chatbot that stops at paragraphs. Its agent maps a short message to a small, typed set of tools that do bounded work on files you choose. Results are verified from the operation that actually ran. No cloud AI account is required.

> **Current status:** early-stage MVP. The local workflow and safety boundaries are being built first. Do not expose this single-user development server to the internet or treat it as enterprise production software yet.

## What it does today

- **Find files** in a private local workspace.
- **Detect duplicates** using SHA-256 content hashes.
- **Clean CSV files** by normalizing headers/whitespace and removing duplicate rows, then write a separate output.
- **Preview file organization** by file type. Moving files requires a separate confirmation.
- **Create notes** from text you provide.
- **Review operation results** through a bounded action layer and same-origin local web interface.
- **Use an optional local Ollama model** to classify requests into an allowlisted action schema. The model cannot run shell commands or invent tools; deterministic routing works without a model.

The interface is a small, responsive, dark chat UI. The runtime executes actions instead of presenting generated prose as completed work.

## Start locally

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows PowerShell: .venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m uvicorn some.main:app --host 127.0.0.1 --port 8080
```

Open http://127.0.0.1:8080. By default, files live in `~/.some/workspace`. Set `SOME_WORKSPACE` to use another dedicated directory. SOME binds to loopback intentionally; do not change this to `0.0.0.0` without adding real authentication, authorization, TLS, per-user isolation, and deployment hardening.

### Try these messages

- `show my files`
- `find duplicate files`
- `clean customers.csv`
- `organize my files` — shows a preview; review it before confirming
- `create note: Call the supplier on Monday`

Upload controls accept files up to 10 MiB. SOME never executes uploaded files.

## Local model (optional)

Install and start [Ollama](https://ollama.com/) yourself, pull a local instruction model, then configure:

```bash
# Example only; use any compatible model already available locally.
ollama pull qwen2.5:3b
# PowerShell:
$env:SOME_OLLAMA_MODEL="qwen2.5:3b"
# macOS/Linux:
export SOME_OLLAMA_MODEL="qwen2.5:3b"
```

When configured, SOME asks the local model for a JSON action classification. It validates the action against a fixed schema and falls back to deterministic routing if the local model is unavailable or returns invalid output. By default, no user content is sent to a hosted model. Keep Ollama bound to your own machine.

## Principles

1. **Do work, don't perform confidence.** Report a success only when a tool finished and its result is measured.
2. **Preview before mutation.** File moves are previewed and require a second explicit confirmation.
3. **Least privilege.** No shell tool, arbitrary Python execution, browser session, or unrestricted model-generated code.
4. **Private by default.** Local storage, loopback binding, no analytics, no telemetry, no cloud dependency.
5. **Small surface area.** Typed actions, strict path boundaries, upload limits, and visible artifacts.
6. **Honest status.** A local MVP is not marketed as audited, multi-tenant, or production-ready.

## Project status and roadmap

See [ARCHITECTURE.md](docs/ARCHITECTURE.md), [SECURITY.md](docs/SECURITY.md), and [CONTRIBUTING.md](CONTRIBUTING.md). CI now checks Python 3.11–3.13, runs regression tests, compiles Python, and validates front-end JavaScript. CodeQL and Dependabot workflows add scheduled static analysis and dependency update proposals. Remaining priorities: durable action receipts, background jobs for large files, authenticated multi-user isolation before hosted deployment, then carefully scoped integrations (calendar, email, messaging) with explicit permissions.

## License

MIT — see [LICENSE](LICENSE).
