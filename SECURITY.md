# Security

`s1000d-mcp` is a local [MCP](https://modelcontextprotocol.io) server for reviewing S1000D data modules. Its tools read and write XML files and send document content to an LLM, so its attack surface is **untrusted file paths, untrusted XML, and untrusted document text**. Every control below is backed by a test that asserts the attack fails, and the whole set is enforced in CI — a PR can't merge into `main` unless the security scan and the security tests are green.

## Reporting a vulnerability

This is a portfolio project, not a production service. If you find an issue, please open a GitHub issue (or, for something sensitive, a private security advisory on this repo). There's no formal SLA, but reports are welcome.

## Threat model

What the server treats as hostile:

- **Untrusted file paths.** Every tool takes caller-supplied paths. A caller may send an absolute path (`/etc/passwd`), a `../../` escape, or a symlink that points outside the project to read, overwrite, or probe for files it shouldn't reach.
- **Untrusted XML.** A data module may be malformed, enormous, or carry an external-entity (XXE) payload aimed at reading local files, making network calls, or exhausting memory.
- **Untrusted document text.** `suggest_fix` sends document content to an LLM. That content may be crafted to steer the model (prompt injection), and the model's reply is not trusted to conform to its own output contract.

**Deliberately out of scope.** The server runs locally over **stdio** — there is no network endpoint and no multi-tenant boundary — so authentication, per-session authorization, and request-rate limiting are not implemented. If the server were ever exposed over HTTP/SSE, those would become required; see the last section.

## Controls

| Threat | Control | Enforced by |
|---|---|---|
| Path traversal (read / overwrite / existence-oracle / read→API exfil) | `_resolve` confines every path parameter to `BASE_DIR`; absolute paths and `..` escapes are rejected *before* any file is opened or written — one choke point for all four vectors | `tests/security/test_phase1_attacks.py` |
| XXE (file read, SSRF, external DTD, billion-laughs, XInclude) | All XML is parsed with an explicit hardened parser: `resolve_entities=False`, `no_network=True`, `load_dtd=False`. Pinned explicitly so a future lxml default change can't silently re-enable it | `tests/security/test_xxe.py` (with a positive control that *does* leak) |
| Denial of service (memory exhaustion) | File-size and string-length caps (`MAX_FILE_BYTES`, `MAX_STR_LEN`) checked before any parse or read; oversized inputs are rejected cheaply, directory scans skip and continue | `tests/security/test_phase1_attacks.py` |
| Error / host-path leakage | Raw exceptions (absolute paths, library internals) are logged server-side only; callers get generic messages and repo-relative paths | `tests/security/test_phase1_attacks.py` |
| Prompt injection via document content (OWASP LLM01) | The system prompt marks the schema and data module as untrusted **data, not instructions**; document content is segregated from instructions | `tests/security/test_injection.py` |
| Untrusted LLM output (OWASP LLM01) | `suggest_fix` forces a structured `propose_fix` tool call **and** deterministically validates the result (non-empty strings, confidence enum) before returning it — define *and* validate | `tests/security/test_injection.py` |
| Model-cost abuse | `suggest_fix` refuses any `model` outside an operator-controlled allowlist (`S1000D_MCP_ALLOWED_MODELS`), so a caller can't point it at an arbitrarily expensive model | — |
| No autonomous action | `suggest_fix` only returns text; it never applies a change. Human-in-the-loop by design | — |

These map to the **OWASP Top 10 for LLM Applications (2025)** (LLM01 Prompt Injection) and the **OWASP MCP Security Cheat Sheet** (Path & File Handling, Input Validation, Error Handling, Rate Limiting & DoS).

### Configuration knobs

| Variable | Purpose | Default |
|---|---|---|
| `S1000D_MCP_BASE_DIR` | The directory every path parameter is confined to | repo root |
| `S1000D_MCP_MAX_FILE_BYTES` | Max input file size | 10 MB |
| `S1000D_MCP_MAX_STR_LEN` | Max free-text string length | 100 000 |
| `S1000D_MCP_ALLOWED_MODELS` | Models `suggest_fix` may call | `claude-haiku-4-5,claude-sonnet-4-5` |

## How CI enforces it

Every push and PR to `main` — plus a weekly scheduled run — executes `.github/workflows/security.yml`, two required jobs:

- **`scan`** — the reusable [`mcp-security-suite`](https://github.com/i2oss/mcp-security-suite) gate: repo scanners (bandit, pip-audit, gitleaks) plus a black-box protocol fuzz harness that drives this server over stdio and probes every string parameter for traversal, oversized input, output-injection, and error leakage. It exits non-zero on any un-waived finding at or above `high`.
- **`security-tests`** — this repo's own `tests/security/` (the XXE, injection, and Phase 1 attack-regression suites listed above).

Branch protection on `main` requires both checks and allows no bypass, admins included, so hardening can't regress unnoticed. The weekly run means a newly disclosed CVE in a dependency turns the gate red on its own — which is how `pyjwt` (PYSEC-2026-4141, transitive via the MCP SDK) was caught and patched.

## Observability

Each tool call emits an OpenTelemetry span (name, input/output size, duration, error type), with token count and estimated cost on `suggest_fix`. The exporter is file/console only (no network backend); `s1000d-mcp-trace-report` summarizes the trace log into per-tool latency percentiles, error rate, and LLM cost. Spans record sizes, never argument values, so file paths and document text don't leak into traces.

## If this went remote (HTTP/SSE)

Not needed for local stdio, but the honest list of what would become required: per-request authentication and authorization (OAuth 2.0 + PKCE; guard against the confused-deputy problem), per-session/tenant rate limits and quotas, and binding session IDs to user context.
