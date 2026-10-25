"""Settings: built-in defaults, then an optional TOML file, then environment variables.

Nothing here knows about a particular machine. Every path, host and name is a setting.
Unknown keys in the config file are an error, because a misspelt `lock_sensitive` that
is silently ignored is a privacy setting that silently does nothing.
"""
from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

ENV_PREFIX = "HUSHBRAIN_"
CONFIG_ENV = "HUSHBRAIN_CONFIG"
DEFAULT_CONFIG_NAME = "hushbrain.toml"

# pgvector's HNSW index supports up to 2000 dimensions.
MAX_EMBED_DIM = 2000


class ConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    # Storage
    dsn: str = "postgresql:///hushbrain"
    # Vault (indexer only)
    vault_dir: Path | None = None
    nodes_subdir: str = "nodes"
    # Privacy
    policy_path: Path | None = None
    lock_sensitive: bool = False
    # Embeddings: "ollama" for real use, "hash" for tests and offline demos
    embedder: str = "ollama"
    ollama_url: str = "http://localhost:11434"
    embed_model: str = "nomic-embed-text:v1.5"
    embed_dim: int = 768
    # Read tools
    default_search_limit: int = 8
    max_search_limit: int = 20
    summary_chars: int = 200
    changed_since_limit: int = 50
    # Indexer behaviour
    log_file: Path | None = None
    log_level: str = "INFO"
    debounce_ms: int = 400
    reconcile_sec: int = 15
    initial_scan: bool = True

    @property
    def nodes_dir(self) -> Path:
        if self.vault_dir is None:
            raise ConfigError("vault_dir is not set (HUSHBRAIN_VAULT_DIR or vault_dir in the config file)")
        return self.vault_dir / self.nodes_subdir


_PATHS = {"vault_dir", "policy_path", "log_file"}
_BOOLS = {"lock_sensitive", "initial_scan"}
_INTS = {
    "embed_dim", "default_search_limit", "max_search_limit", "summary_chars",
    "changed_since_limit", "debounce_ms", "reconcile_sec",
}
_FIELD_NAMES = {f.name for f in fields(Settings)}


def _coerce(name: str, value: Any) -> Any:
    if name in _PATHS:
        if value in (None, ""):
            return None
        return Path(str(value)).expanduser()
    if name in _BOOLS:
        if isinstance(value, bool):
            return value
        s = str(value).strip().lower()
        if s in {"1", "true", "yes", "on"}:
            return True
        if s in {"0", "false", "no", "off", ""}:
            return False
        raise ConfigError(f"{name}: cannot read {value!r} as a boolean")
    if name in _INTS:
        try:
            return int(value)
        except (TypeError, ValueError) as e:
            raise ConfigError(f"{name}: cannot read {value!r} as an integer") from e
    return str(value)


def _find_config(explicit: Path | str | None, env: Mapping[str, str]) -> Path | None:
    if explicit:
        return Path(explicit).expanduser()
    if env.get(CONFIG_ENV):
        return Path(env[CONFIG_ENV]).expanduser()
    local = Path.cwd() / DEFAULT_CONFIG_NAME
    return local if local.is_file() else None


def load_settings(
    config_path: Path | str | None = None, env: Mapping[str, str] | None = None
) -> Settings:
    env = os.environ if env is None else env
    values: dict[str, Any] = {}

    path = _find_config(config_path, env)
    if path is not None:
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)
        except (OSError, tomllib.TOMLDecodeError) as e:
            raise ConfigError(f"cannot read config {path}: {e}") from e
        unknown = sorted(set(data) - _FIELD_NAMES)
        if unknown:
            raise ConfigError(f"unknown setting(s) in {path}: {', '.join(unknown)}")
        values.update(data)

    for name in _FIELD_NAMES:
        key = ENV_PREFIX + name.upper()
        if key in env:
            values[name] = env[key]

    settings = Settings(**{k: _coerce(k, v) for k, v in values.items()})
    _validate(settings)
    return settings


def _validate(s: Settings) -> None:
    if s.embedder not in {"ollama", "hash"}:
        raise ConfigError(f"embedder must be 'ollama' or 'hash', got {s.embedder!r}")
    if not 1 <= s.embed_dim <= MAX_EMBED_DIM:
        raise ConfigError(f"embed_dim must be between 1 and {MAX_EMBED_DIM}")
    if s.default_search_limit < 1 or s.max_search_limit < s.default_search_limit:
        raise ConfigError("search limits must satisfy 1 <= default_search_limit <= max_search_limit")
    if s.summary_chars < 20:
        raise ConfigError("summary_chars must be at least 20")
