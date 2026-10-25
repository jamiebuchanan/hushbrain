from __future__ import annotations

import pytest

from conftest import HIDDEN, NORMAL_IDS, SAMPLE_VAULT
from hushbrain.notes import content_hash, parse_note, safe_name


def parse(policy, name):
    return parse_note(SAMPLE_VAULT / "nodes" / f"{name}.md", policy)


@pytest.mark.parametrize("node_id", sorted(NORMAL_IDS))
def test_normal_notes_are_normal(policy, node_id):
    n = parse(policy, node_id)
    assert n.sensitivity == "normal"
    assert n.sensitivity_reason == "explicit"


@pytest.mark.parametrize("node_id,expected", sorted((k, v[0]) for k, v in HIDDEN.items()))
def test_hidden_notes_are_stored_restricted(policy, node_id, expected):
    assert parse(policy, node_id).sensitivity == expected


def test_reasons(policy):
    assert parse(policy, "note-draft-salary-review").sensitivity_reason == "missing"
    assert parse(policy, "note-persnal-typo").sensitivity_reason == "invalid"
    assert parse(policy, "note-cottage-booking").sensitivity_reason == "project-floor"
    assert parse(policy, "note-family-weekend-plans").sensitivity_reason == "explicit"


def test_edges_and_extras(policy):
    n = parse(policy, "fact-supplier-lead-time-2026-03")
    assert n.edges == [{"type": "derived_from", "target": "source-trade-body-pricing-survey", "note": ""}]
    assert parse(policy, "source-trade-body-pricing-survey").extra["url"].startswith("https://example.com")


def test_id_may_differ_from_filename(policy, tmp_path):
    p = tmp_path / "some-file.md"
    p.write_text("---\nid: other-id\nsensitivity: normal\n---\nBody text.\n")
    n = parse_note(p, policy)
    assert n.node_id == "other-id"
    assert n.source_file == "some-file.md"


def test_unknown_type_becomes_note_and_summary_is_derived(policy, tmp_path):
    p = tmp_path / "x.md"
    p.write_text("---\ntype: gadget\nsensitivity: normal\n---\n# Heading\n\n> quote\nFirst real line.\n")
    n = parse_note(p, policy)
    assert n.ntype == "note"
    assert n.summary == "First real line."
    assert n.title == "x"


def test_malformed_frontmatter_returns_none(policy, tmp_path):
    p = tmp_path / "bad.md"
    p.write_text("---\nkey: [unclosed\n---\nbody\n")
    assert parse_note(p, policy) is None


def test_no_frontmatter_at_all_is_private(policy, tmp_path):
    p = tmp_path / "plain.md"
    p.write_text("Just text, no frontmatter.\n")
    n = parse_note(p, policy)
    assert n.sensitivity == "personal"
    assert n.sensitivity_reason == "missing"


def test_content_hash_separates_summary_from_body():
    assert content_hash("a", "b") != content_hash("ab", "")
    assert content_hash("same", "") == content_hash("same", "same")  # empty body embeds the summary
    assert content_hash("s", "b") == content_hash("s", "b")


def test_safe_name_is_short_and_stable():
    assert safe_name("note-x.md") == safe_name("note-x.md")
    assert len(safe_name("note-x.md")) == 8
    assert "note" not in safe_name("note-x.md")
