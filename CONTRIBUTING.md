# Contributing to SOME

SOME is intended to perform narrow, verifiable actions rather than produce long answers. Contributions should preserve that principle.

## Development loop

1. Create a focused issue describing the real-world task and failure mode.
2. Keep each tool typed, bounded, and independently testable.
3. Add tests for successful behavior, invalid input, path traversal, symlinks, and failure cases.
4. Run \`pytest\` and \`ruff check .\` before opening a pull request.
5. Explain data flow, external services, permissions, and whether user content leaves the machine.

## Action design rules

- Never execute model-generated shell commands, code, SQL, or arbitrary URLs.
- Keep model output limited to an allowlisted action name and validated arguments.
- Require explicit confirmation for destructive or externally visible changes.
- Return a result derived from the operation that actually completed.
- Do not silently overwrite a user's file; write a new artifact or ask first.
- Add a safe maximum size / time budget for untrusted inputs.
- Do not add telemetry or hosted-model calls by default.

## Licensing

Submit only original work or dependencies whose licenses permit the intended use. Document new runtime dependencies and their licenses. Do not copy code from repositories unless its license and attribution requirements have been reviewed.

## Security reports

Please do not put exploitable vulnerabilities, private data, or access tokens in a public issue. See SECURITY.md for reporting guidance.
