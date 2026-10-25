from __future__ import annotations

from pathlib import Path

import pytest

from hushbrain.config import ConfigError, load_settings


def test_defaults_need_no_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = load_settings(env={})
    assert s.lock_sensitive is False
    assert s.embedder == "ollama"
    assert s.vault_dir is None


def test_file_then_env_precedence(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text('dsn = "postgresql:///from_file"\nembed_dim = 100\nlock_sensitive = false\n')
    s = load_settings(f, env={"HUSHBRAIN_DSN": "postgresql:///from_env"})
    assert s.dsn == "postgresql:///from_env"
    assert s.embed_dim == 100


def test_config_path_from_env(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text("embed_dim = 128\n")
    assert load_settings(env={"HUSHBRAIN_CONFIG": str(f)}).embed_dim == 128


def test_unknown_key_is_an_error_not_silently_ignored(tmp_path):
    f = tmp_path / "c.toml"
    f.write_text("lock_sensetive = true\n")
    with pytest.raises(ConfigError, match="lock_sensetive"):
        load_settings(f, env={})


@pytest.mark.parametrize("raw,expected", [("1", True), ("true", True), ("ON", True),
                                          ("0", False), ("false", False), ("", False)])
def test_bool_env(raw, expected, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert load_settings(env={"HUSHBRAIN_LOCK_SENSITIVE": raw}).lock_sensitive is expected


def test_garbage_bool_is_refused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError, match="boolean"):
        load_settings(env={"HUSHBRAIN_LOCK_SENSITIVE": "maybe"})


def test_paths_expand_user(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = load_settings(env={"HUSHBRAIN_VAULT_DIR": "~/somewhere"})
    assert s.vault_dir == Path("~/somewhere").expanduser()
    assert s.nodes_dir == Path("~/somewhere").expanduser() / "nodes"


@pytest.mark.parametrize("env", [
    {"HUSHBRAIN_EMBEDDER": "magic"},
    {"HUSHBRAIN_EMBED_DIM": "0"},
    {"HUSHBRAIN_EMBED_DIM": "2001"},
    {"HUSHBRAIN_EMBED_DIM": "many"},
    {"HUSHBRAIN_DEFAULT_SEARCH_LIMIT": "30"},
])
def test_invalid_values_are_refused(env, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ConfigError):
        load_settings(env=env)
