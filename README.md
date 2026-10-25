# hushbrain

An MCP memory server over a vault of typed markdown notes, where personal and legal notes stay out of every read unless the caller explicitly opts in.

I built it for a large vault of notes that an AI assistant searches all day: project decisions, people and facts, and also the private kind: the notes you would never want a work search to surface. The failure I was designing against is not a hacker. It is a search for "what did we decide about pricing" surfacing a private note, which then ends up in a client email, a commit message or a Slack post. So privacy here is the default of the read path, not a setting you remember to turn on.

> Status: v0.1 skeleton. It runs and its tests pass, but see [ROADMAP.md](ROADMAP.md) for what is not done and what still needs checking before a public release.

## The idea in four rules

1. **Summaries first, bodies on request.** `search`, `recent`, `project_context` and the other discovery tools return a one-line summary per note and never a body. The only way to read a full note is a separate `get_body(id)` call. A model can scan a page of candidate summaries before paying to read any full note.
2. **Every note has a sensitivity tier**: `normal`, `personal` or `legal` (you can define your own in the policy).
3. **Every read tool excludes everything but the default-visible tiers** unless the caller passes `include_sensitive=true`. That includes `get_body`, which refuses a hidden note and returns no content.
4. **A note with no label is private.** So is a note whose label is misspelt or unknown, and so is any note filed under a project your policy marks as private. Forgetting to label a note can only hide it, never expose it.

Servers in this space usually partition memory by user, project or agent. As far as I could tell from reading the front pages of [mem0](https://github.com/mem0ai/mem0) (scoping by `user_id`) and [mcp-memory-service](https://github.com/doobidoo/mcp-memory-service) (stores, tags, agent ids) on 23 October 2026, neither describes a sensitivity tier that hides memories from default retrieval. That is a README-level reading, not a code review, so check before you rely on the comparison.

## What is in the box

| Piece | What it does |
|---|---|
| `hushbrain serve` | The MCP server on stdio. Seven tools: `search`, `cross_project`, `recent`, `project_context`, `what_changed_since`, `get_body`, `stats`. |
| `hushbrain index [--watch]` | Reads a vault of markdown notes, resolves each note's tier through the policy, embeds the summary and body, and writes Postgres + pgvector. With `--watch` it follows changes, with a reconciliation sweep for the times a file watcher drops events. |
| `hushbrain init-db` | Creates the tables. |
| `hushbrain check-policy` | Validates a policy file and prints tier names and counts, never project names, terms or patterns. |
| `hushbrain audit <vault>` | Shows how a vault would be labelled and exported, with no database needed. `--list` names the files whose tier did not come from an explicit label of their own. |
| `examples/policy.example.toml` | A policy with invented values to copy from. |
| `sample-vault/` | Thirteen invented notes, six of them private or mislabelled on purpose, used by the tests and the demo. |

## Quick start

You need Docker and [uv](https://docs.astral.sh/uv/). The code declares Python 3.11 or later and has been run on 3.12 only. This path uses the built-in `hash` embedder so it works without Ollama. It matches words, it does not understand meaning, so use it to see the privacy behaviour and swap in Ollama (below) for real recall.

```bash
uv sync
cp .env.example .env                       # then edit the password
docker compose up -d --wait db             # Postgres 17 with pgvector, on 127.0.0.1:54329

export HUSHBRAIN_DSN="postgresql://hushbrain:<your password>@127.0.0.1:54329/hushbrain"
export HUSHBRAIN_EMBEDDER=hash HUSHBRAIN_EMBED_DIM=64
export HUSHBRAIN_POLICY_PATH=examples/policy.example.toml
export HUSHBRAIN_VAULT_DIR=sample-vault

uv run hushbrain init-db
uv run hushbrain index
uv run hushbrain audit sample-vault --list
```

Then register the server with your MCP client. For Claude Code, the config in [`examples/claude-code-mcp.example.json`](examples/claude-code-mcp.example.json) shows the shape (replace `/path/to`). There is a second example with the lock switched on.

### Real embeddings

Install [Ollama](https://ollama.com), then `ollama pull nomic-embed-text:v1.5` and leave the defaults (`embedder = "ollama"`, 768 dimensions). The dimension is fixed when the store is created; changing it later means a new store.

### Your own vault

A vault is a folder with a flat `nodes/` directory of markdown files:

```markdown
---
id: decision-no-peat-compost
type: decision
title: Stop stocking peat compost
summary: We stopped stocking peat based compost from the 2026 season because customers now ask for peat free first.
created: 2026-02-10
updated: 2026-02-10
project_id: garden-centre
sensitivity: normal          # normal | personal | legal. Leave it out and the note is private.
edges:
  - type: part_of
    target: pillar-garden-centre
---

## Decision
...
```

Write the `summary` for a machine reader: one sentence, specific, under 200 characters. It is what retrieval runs on.

## Configuration

Settings come from built-in defaults, then an optional TOML file (`HUSHBRAIN_CONFIG`, or `hushbrain.toml` in the working directory), then environment variables named `HUSHBRAIN_` plus the key in capitals. Unknown keys in the file are an error, because a misspelt privacy setting that is silently ignored does nothing. The full list is in [`examples/hushbrain.example.toml`](examples/hushbrain.example.toml). The ones that matter most:

| Setting | Meaning |
|---|---|
| `dsn` | Postgres connection string. |
| `vault_dir`, `nodes_subdir` | Where the indexer reads notes. |
| `policy_path` | The sensitivity policy. Without one, a strict built-in policy applies. |
| `lock_sensitive` | When true, `include_sensitive` is ignored for every caller. Use it for any session or agent that should never see private notes, however it is prompted. |
| `embedder`, `embed_dim`, `ollama_url`, `embed_model` | Embeddings. `hash` is for tests and demos only. |

## The policy

One small TOML file read by the server, the indexer and the command line. See [`examples/policy.example.toml`](examples/policy.example.toml) (every value in it is invented).

- `[tiers]`: the tier names from least to most restricted, which are visible by default, and what an unlabelled note becomes. The loader refuses a policy where the unlabelled tier is `normal` or is visible by default, because that would fail open.
- `[projects]`: tier floors. A note filed under a listed project is stored at least that restricted, whatever its own label says. Labels can be raised, never lowered.
- `[export]`: an egress gate for anything that leaves the machine, default deny, with tier, type, project and blocklist rules. `hushbrain audit` reports what it would admit. Wiring it to an actual export command is on the roadmap.

If the policy file is unreadable or invalid, the server falls back to the strictest built-in policy and logs why. The indexer and `hushbrain audit` refuse to run instead: the built-in policy has no project floors, so indexing under it would re-store a floored note at its own, lower label. With no policy file configured at all, the built-in policy applies everywhere.

## Limits you should know about

**This is a relevance filter that prevents accidents. It is not access control.**

- Anyone who can read the database or the vault reads everything in it. Nothing is encrypted at rest.
- `include_sensitive` is a parameter the calling model chooses. A prompt-injected or careless model can pass it. Two mitigations are included: `lock_sensitive` for sessions that must never see private notes, and the tool schema declares the default as false and accepts only a JSON boolean (the string `"true"` or the number `1` is an error, not an opt-in), so a host-side hook can match on `include_sensitive: true` and ask a human first. Per-caller tiers are on the roadmap.
- `get_body` names the tier of a withheld note. That is deliberate (the caller learns the note exists and needs an opt-in), and it also means the existence of a private note id is not hidden from someone who already knows the id.
- `stats` reports one `withheld` number so a caller can tell something is hidden. It never says what, or how many per tier, unless the caller opts in.
- The indexer logs notes outside the default-visible tiers under a short hash, not by id, since a note id is often enough to disclose its subject.
- Tested on macOS with Postgres 17 and pgvector. Linux is expected to work and has not been tried. Windows is untested.

## Tests

```bash
bash scripts/test.sh
```

This starts a throwaway pgvector container on 127.0.0.1 with a random password, runs the suite, and removes the container. It fails if any test was skipped, because a skipped database test is a privacy test that did not run. Set `HUSHBRAIN_TEST_DSN` to use your own server instead (the user needs `CREATE DATABASE`; each test makes and drops its own database).

What the suite proves, against a real Postgres and the synthetic sample vault:

- Every read tool, called with no flag, leaves personal and legal notes out of the entire response: ids, titles, summaries and planted marker text. The same calls with the flag do return them, so the filter is what hides them.
- `get_body` refuses every private, unlabelled, misspelt and project-floored note and returns no content; it returns normal notes.
- Only a literal `true` opts in. The strings `"true"` and `"false"`, `1`, and `null` do not: the store ignores them, and the MCP tool boundary rejects them as errors.
- A row with a tier the policy has never heard of is hidden, and the column has no default.
- Every MCP tool declares `include_sensitive` with a default of false, so a tool added later without it fails the suite.
- Re-labelling a note takes effect even though its content hash is unchanged, deleted files remove their rows, and the logs never name a private note.
- The real `hushbrain serve` process, driven over stdio, hides private notes by default.

## Credits

The node and edge taxonomy (16 node types, 10 typed edges, one-sentence summaries read before bodies) follows the infinite brain approach from AI Impact's video [Don't Use Karpathy's Second Brain (I BUILT SOMETHING BETTER)](https://www.youtube.com/watch?v=z02Y-1OvWSM). The `project` node type, the sensitivity tiers, the policy file, the indexer and the server are this project's own. Built on the [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk), [pgvector](https://github.com/pgvector/pgvector), [psycopg](https://www.psycopg.org/), [watchdog](https://github.com/gorakhargosh/watchdog) and [python-frontmatter](https://github.com/eyeseast/python-frontmatter).

## Licence

MIT. See [LICENSE](LICENSE).
