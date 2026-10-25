from __future__ import annotations

import pytest

from hushbrain.policy import PolicyError, builtin, load_or_builtin, parse

BASE = {
    "version": 1,
    "tiers": {
        "levels": ["normal", "personal", "legal"],
        "default_visible": ["normal"],
        "default_for_missing": "personal",
    },
}


def with_(**overrides):
    d = {**BASE, "tiers": {**BASE["tiers"]}}
    for k, v in overrides.items():
        if k.startswith("tiers_"):
            d["tiers"][k[len("tiers_"):]] = v
        else:
            d[k] = v
    return d


def test_example_policy_loads(policy):
    assert policy.levels == ("normal", "personal", "legal")
    assert policy.default_visible == ("normal",)
    assert policy.default_for_missing == "personal"


@pytest.mark.parametrize("raw,tier,reason", [
    ("normal", "normal", "explicit"),
    ("PERSONAL", "personal", "explicit"),
    ("  legal ", "legal", "explicit"),
    (None, "personal", "missing"),
    ("", "personal", "missing"),
    ("   ", "personal", "missing"),
    ("persnal", "personal", "invalid"),
    ("public", "personal", "invalid"),
])
def test_resolve_tier_fails_closed(policy, raw, tier, reason):
    assert policy.resolve_tier(raw) == (tier, reason)


def test_project_floor_only_raises(policy):
    assert policy.apply_project_floor("normal", "family-holiday") == ("personal", True)
    assert policy.apply_project_floor("normal", "FAMILY-HOLIDAY") == ("personal", True)
    assert policy.apply_project_floor("legal", "family-holiday") == ("legal", False)
    assert policy.apply_project_floor("normal", "orchard-lane-dispute") == ("legal", True)
    assert policy.apply_project_floor("normal", "garden-centre") == ("normal", False)
    assert policy.apply_project_floor("normal", None) == ("normal", False)


def test_visible_tiers_only_a_literal_true_opts_in(policy):
    assert policy.visible_tiers(True) is None
    for not_true in (False, None, 0, 1, "true", "false", "yes", [], {}):
        assert policy.visible_tiers(not_true) == ("normal",)


@pytest.mark.parametrize("d,msg", [
    (with_(version=2), "unsupported"),
    (with_(tiers_levels=["personal", "normal"]), "start with 'normal'"),
    (with_(tiers_levels=["normal", "personal", "personal"]), "duplicates"),
    (with_(tiers_default_visible=["normal", "ghost"]), "not a subset"),
    (with_(tiers_default_for_missing="normal"), "non-normal"),
    (with_(tiers_default_for_missing="ghost"), "non-normal"),
    (with_(tiers_default_visible=["normal", "personal"]), "must not be in"),
    (with_(projects={"ghost": ["x"]}), "projects.ghost"),
    (with_(projects={"normal": ["x"]}), "projects.normal"),
    (with_(export={"allow_tiers": ["ghost"]}), "allow_tiers"),
    (with_(export={"blocklist_regex": ["(unclosed"]}), "does not compile"),
    (with_(export={"deny_types": "contact"}), "list of strings"),
])
def test_invalid_policies_are_refused(d, msg):
    with pytest.raises(PolicyError, match=msg):
        parse(d)


def test_project_in_two_tiers_takes_the_stricter(policy):
    p = parse(with_(projects={"personal": ["x"], "legal": ["x"]}))
    assert p.project_floors["x"] == "legal"


def test_load_or_builtin_falls_back_strict(tmp_path, caplog):
    assert load_or_builtin(None) == builtin()
    assert load_or_builtin(tmp_path / "missing.toml") == builtin()
    bad = tmp_path / "bad.toml"
    bad.write_text("version = 1\n[tiers]\nlevels = ['personal']\n")
    p = load_or_builtin(bad)
    assert p == builtin()
    assert p.default_visible == ("normal",)
    assert p.default_for_missing == "personal"


def test_export_gate_default_deny(policy):
    ok = {"sensitivity": "normal", "type": "fact", "project_id": "garden-centre",
          "title": "Lead time", "summary": "Eight days", "body": "Plain text"}
    assert policy.export_decision(ok) == (True, "ok")
    assert policy.export_decision({**ok, "sensitivity": "personal"}) == (False, "tier")
    assert policy.export_decision({k: v for k, v in ok.items() if k != "sensitivity"}) == (False, "tier")
    assert policy.export_decision({**ok, "type": "contact"}) == (False, "type")
    assert policy.export_decision({**ok, "type": "note"}) == (False, "type-not-allowed")
    assert policy.export_decision({**ok, "project_id": "Internal-Payroll"}) == (False, "project")


def test_export_blocklist(policy):
    ok = {"sensitivity": "normal", "type": "fact", "title": "t", "summary": "s", "body": "b"}
    assert policy.export_decision({**ok, "body": "see Wren and Pike"}) == (False, "blocklist:body")
    assert policy.export_decision({**ok, "body": "wren_and-pike"}) == (False, "blocklist:body")
    assert policy.export_decision({**ok, "title": "Salary review"}) == (False, "blocklist:title")
    # word terms match whole words only
    assert policy.export_decision({**ok, "title": "Salaried staff rota"}) == (True, "ok")
    assert policy.export_decision({**ok, "summary": "ref INV-123456 paid"}) == (False, "blocklist:summary")
    assert policy.export_decision({**ok, "summary": "ref INV-12345 paid"}) == (True, "ok")
