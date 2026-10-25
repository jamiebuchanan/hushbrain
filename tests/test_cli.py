from __future__ import annotations

from conftest import EXAMPLE_POLICY, SAMPLE_VAULT
from hushbrain.cli import main


def test_audit_reports_counts_and_names_problem_files_only_on_request(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HUSHBRAIN_POLICY_PATH", str(EXAMPLE_POLICY))
    assert main(["audit", str(SAMPLE_VAULT)]) == 0
    out = capsys.readouterr().out
    assert "notes: 13" in out
    assert "'normal': 7" in out and "'personal': 5" in out and "'legal': 1" in out
    assert "'missing': 1" in out and "'invalid': 1" in out and "'project-floor': 1" in out
    assert "'admitted': 7" in out and "'refused:tier': 6" in out
    assert "note-persnal-typo" not in out

    assert main(["audit", str(SAMPLE_VAULT), "--list"]) == 0
    out = capsys.readouterr().out
    assert "note-persnal-typo.md  invalid" in out
    assert "note-draft-salary-review.md  missing" in out
    assert "note-cottage-booking.md  project-floor" in out


def test_check_policy_prints_counts_never_values(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert main(["check-policy", str(EXAMPLE_POLICY)]) == 0
    out = capsys.readouterr().out
    assert "policy ok" in out
    for value in ("family-holiday", "orchard-lane-dispute", "wren and pike", "internal-payroll", "salary"):
        assert value not in out


def test_check_policy_rejects_a_policy_that_would_fail_open(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / "bad.toml"
    bad.write_text('version = 1\n[tiers]\nlevels = ["normal", "personal"]\n'
                   'default_visible = ["normal"]\ndefault_for_missing = "normal"\n')
    assert main(["check-policy", str(bad)]) == 2
    assert "default_for_missing" in capsys.readouterr().err


def test_a_typo_in_the_config_file_stops_the_command(capsys, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "c.toml"
    cfg.write_text("lock_sensetive = true\n")
    assert main(["--config", str(cfg), "audit", str(SAMPLE_VAULT)]) == 2
    assert "lock_sensetive" in capsys.readouterr().err


def test_index_and_audit_refuse_a_broken_policy_instead_of_falling_back(capsys, monkeypatch, tmp_path):
    """The built-in fallback has no project floors, so indexing under it would re-store a
    floored note at its own, lower label. The writer and the reporter must stop instead."""
    monkeypatch.chdir(tmp_path)
    bad = tmp_path / "bad.toml"
    bad.write_text("version = 1\n[tiers]\nlevels = ['personal']\n")
    monkeypatch.setenv("HUSHBRAIN_VAULT_DIR", str(SAMPLE_VAULT))
    for policy_path in (bad, tmp_path / "does-not-exist.toml"):
        monkeypatch.setenv("HUSHBRAIN_POLICY_PATH", str(policy_path))
        assert main(["index"]) == 2
        assert "error:" in capsys.readouterr().err
        assert main(["audit", str(SAMPLE_VAULT)]) == 2
        assert "error:" in capsys.readouterr().err
