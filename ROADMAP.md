# Roadmap

State on 23 October 2026: a runnable v0.1 skeleton. The server, indexer, policy loader, schema, Docker setup and a privacy-focused test suite exist and pass. This file lists what is left.

## Before a 1.0 release

- **Verify the comparison claim.** The README says mem0 and mcp-memory-service describe no sensitivity tier, from reading their front pages. Re-check against current docs and code on the day of release.
- **Secret scanning in CI.** `trufflehog` found no secrets in the tree or the history on 24 October 2026. Add `gitleaks` and a secret scan to CI.
- **CI.** A GitHub Actions workflow that runs the suite against a `pgvector/pgvector:pg17` service container, on Linux and macOS, failing on skipped tests as `scripts/test.sh` does.
- **A clean-machine run of the README.** The quick start was run once on the build machine. Run it on a fresh Linux box and a fresh Mac, including the Claude Code registration step, which was written from the docs and not executed.
- **Threat model page.** A short `docs/` page expanding the README's limits section: what the filter protects against, what it does not, and how to pair it with host-side hooks that ask a human before a call carrying `include_sensitive: true`.

## Privacy features not built yet

- **Per-caller tiers.** Today one global flag gates everything, and the model chooses it. Map an authenticated client or token to the set of tiers it may request, so a subagent can be structurally unable to opt in. `lock_sensitive` is the blunt first version.
- **Opt-in audit log.** Record each call that passed `include_sensitive`, with the tool and time but never the note ids or text.
- **Capture-time classifier.** Configured terms and project ids that raise a note's tier automatically when the title or body matches (labels only go up). v0.1 has project floors only.
- **A real export command.** `policy.export_decision` and `hushbrain audit` exist, but nothing yet builds a bundle. It should fail closed, and re-scan its own output before writing it.
- **Edges and traversal.** Typed edges are read from frontmatter and kept as text only. Add an edges table and a traverse tool that filters on both ends, so a normal note's edge cannot reveal that a private note exists.
- **Prune after downtime.** Files deleted while the indexer was not running leave stale rows, which for a private note is a privacy bug. Add a startup prune with a mass-delete guard (refuse above a set fraction of rows unless forced).
- **Reduce what `get_body` reveals.** Option to answer "no node" for a withheld id instead of naming its tier, for people who prefer not to confirm existence.

## Product work

- **Ollama embedder against a live Ollama.** Only truncation and the hash embedder are tested. Add a mocked-HTTP test for retries and dimension mismatch, and one run against a real model.
- **Script the mutation pass.** Commit it as a check that deliberately breaks the privacy code paths and confirms the suite fails.
- **Real watcher test.** The watch loop was smoke-tested by hand on macOS (create, edit, delete, SIGTERM). Add an automated test, and try inotify on Linux.
- **Body search.** Body embeddings are stored but nothing queries them.
- **Hybrid search.** Add lexical matching beside the vector search, and tune the HNSW index (`ef_search`).
- **Vault tooling.** A `validate` command (id equals filename, summary length, broken edge targets, oversize bodies), node templates and an `init` scaffold. The author's equivalents live in a separate skill and have not been moved here.
- **Migrations.** `hushbrain_meta` records a schema version but there is no migration runner. A path for anyone with an existing pgvector `memories` table is also missing (the new table adds `source_file` and makes the embeddings NOT NULL).
- **MCP SDK 2.x.** Pinned to `mcp<2` because 2.x renamed `FastMCP` to `MCPServer`. Port and test.
- **HTTP transport with auth**, for use beyond stdio on one machine.
- **Packaging.** Lint and type-check config, a PyPI release, a changelog, a version policy.
- **Platforms.** Linux and Windows are untested. Python 3.11 and 3.13 are declared but only 3.12 was run.

## Done in v0.1

- Server with seven tools, all defaulting to private; `get_body` refuses hidden notes.
- Indexer with debounce, reconciliation sweep, content-hash embed skipping, tier written on every path, file-based deletes, log redaction.
- Policy loader with fail-closed validation, project floors, export gate, strict built-in fallback.
- Config by file and environment, with unknown keys rejected.
- Postgres + pgvector via Docker Compose; schema with no default on the tier column.
- Synthetic sample vault (13 notes) and 297 tests, including an end-to-end stdio test. A one-off mutation pass (13 deliberate regressions in the visibility, `get_body`, policy and indexer code, all caught) was run by hand; the script is not in the repo yet.
