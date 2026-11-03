# Install hushbrain (agent guide)

You are installing hushbrain for the user: an MCP memory server over a vault of typed markdown notes, where personal and legal notes stay out of every read unless the caller explicitly opts in. Follow the sections in order, run each command, and report what you see. Do not change any code.

## Prerequisites

Run each check. Stop and tell the user if a required one fails (git, uv, Python 3.11 or later).

```bash
git --version                 # any recent git
uv --version                  # https://docs.astral.sh/uv/ ; install it first if missing
docker --version              # optional: only needed for the database in "Verify live"
docker compose version        # only needed for the database in "Verify live"
```

The code needs Python 3.11 or later and has been run on 3.12 only. `uv` picks the version in `.python-version` (3.12) and downloads it if it is missing. If `uv` cannot download it, set `export UV_PYTHON=<path to a Python 3.11 or later already on this machine>` once in the shell (`uv python list --only-installed` shows what is there), so every `uv` command below uses it.

## Locate config

hushbrain is a folder you clone, not a global package. Check for an existing install first (run these from the folder where you would clone it):

```bash
ls -d hushbrain 2>/dev/null && git -C hushbrain remote -v
echo "${HUSHBRAIN_CONFIG:-unset}"
```

If a `hushbrain` folder with this remote already exists, use it and skip the clone. Otherwise clone it into the current directory. Settings come from built-in defaults, then an optional TOML file (`HUSHBRAIN_CONFIG`, or `hushbrain.toml` in the working directory), then environment variables named `HUSHBRAIN_` plus the key in capitals. Do not create a config file during install: the example policy and the sample vault are enough to validate.

## Install

```bash
git clone https://github.com/jamiebuchanan/hushbrain.git
cd hushbrain
uv sync
```

## Credentials

There are no API keys. These names are used, and you must never print, log or write a value for them:

- `HUSHBRAIN_DB_PASSWORD`: the Postgres password, in the git-ignored `.env` file (copy `.env.example` to `.env`). The user chooses it.
- `HUSHBRAIN_DSN`: the Postgres connection string, exported in the shell. It contains the same password. The user sets it.

Only the "Verify live" section needs them. "Validate" needs neither.

## Validate

Run it from inside the `hushbrain` folder. It needs no database and no Docker. It checks the example policy and audits the sample vault, and prints one line:

```bash
uv run hushbrain check-policy examples/policy.example.toml >/dev/null \
  && HUSHBRAIN_POLICY_PATH=examples/policy.example.toml uv run hushbrain audit sample-vault --list >/dev/null \
  && echo "ok: hushbrain policy valid and sample vault audited"
```

If it does not print that line, report the error text and stop.

## Verify live

This is the real-use check. It needs Docker and the user's own database password, so it is not for a sandbox. Run it only on the user's machine, and only with their say-so. It is the README Quick start:

```bash
cp .env.example .env                       # then the user edits the password
docker compose up -d --wait db             # Postgres 17 with pgvector, on 127.0.0.1:54329

export HUSHBRAIN_DSN="postgresql://hushbrain:<the user's password>@127.0.0.1:54329/hushbrain"
export HUSHBRAIN_EMBEDDER=hash HUSHBRAIN_EMBED_DIM=64
export HUSHBRAIN_POLICY_PATH=examples/policy.example.toml
export HUSHBRAIN_VAULT_DIR=sample-vault

uv run hushbrain init-db
uv run hushbrain index
uv run hushbrain audit sample-vault --list
```

The `hash` embedder matches words and does not understand meaning. For real recall the user installs Ollama, runs `ollama pull nomic-embed-text:v1.5` and leaves the defaults.

Then register the server with the user's MCP client. For Claude Code, `examples/claude-code-mcp.example.json` shows the shape: a server named `hushbrain` that runs `uv run --quiet --directory <path to this folder> hushbrain serve`, with `HUSHBRAIN_CONFIG` pointing at the user's own `hushbrain.toml`. Replace the `/path/to` values with real paths for the user's machine. `examples/claude-code-mcp-locked.example.json` is the same with the lock switched on.

## Safety rules

- hushbrain is a relevance filter that prevents accidents. It is not access control. Anyone who can read the database or the vault reads everything in it, and nothing is encrypted at rest.
- Do not pass `include_sensitive=true` to any tool unless the user asks for private notes in that request. It is off by default on purpose.
- A note with no label is private, so is one with a misspelt label, and so is one filed under a project the policy marks as private. Do not "fix" labels to make notes visible.
- `get_body` refuses a hidden note. Do not work around it by reading the vault or the database directly.
- Never print, log or commit a database password or a `HUSHBRAIN_DSN` value, and keep `.env` out of git.
- Make no code changes and do not edit the policy. Only run the commands in this guide.
