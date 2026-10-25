"""The core promise, proved on a real Postgres with pgvector and the synthetic sample vault.

Every read tool, called without include_sensitive, must leave personal and legal notes out of
the ENTIRE response (ids, titles, summaries, markers), not just out of the results list.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest

from conftest import (
    DIM, HIDDEN, HIDDEN_IDS, HIDDEN_SUMMARY_FRAGMENTS, HIDDEN_TITLES, MARKERS, NORMAL_IDS,
)
from hushbrain.embeddings import HashEmbedder
from hushbrain.store import MemoryStore

pytestmark = pytest.mark.db


def dump(result) -> str:
    return json.dumps(result, default=str)


def assert_clean(result):
    """No hidden id, planted marker, title or summary text anywhere in the response."""
    text = dump(result).lower()
    for hid in HIDDEN_IDS:
        assert hid not in text, f"hidden id {hid} leaked"
    for marker in MARKERS:
        assert marker not in text, f"marker {marker} leaked"
    for title in HIDDEN_TITLES:
        assert title not in text, f"hidden title {title!r} leaked"
    for fragment in HIDDEN_SUMMARY_FRAGMENTS:
        assert fragment not in text, f"hidden summary text {fragment!r} leaked"


# One call per read tool, each with a query or argument that WOULD match hidden notes.
READ_CALLS = {
    "search": lambda s, **kw: s.search("family weekend knee physio salary fence cottage", limit=20, **kw),
    "search_by_type": lambda s, **kw: s.search("fence settlement", type="decision", **kw),
    "cross_project": lambda s, **kw: s.cross_project("private legal health family", limit=20, **kw),
    "recent": lambda s, **kw: s.recent(limit=20, **kw),
    "recent_by_type": lambda s, **kw: s.recent(limit=20, type="note", **kw),
    "project_context_legal": lambda s, **kw: s.project_context("orchard-lane-dispute", **kw),
    "project_context_personal": lambda s, **kw: s.project_context("family-holiday", **kw),
    "what_changed_since": lambda s, **kw: s.what_changed_since("2000-01-01T00:00:00+00:00", **kw),
    "stats": lambda s, **kw: s.stats(**kw),
}


@pytest.mark.parametrize("name", sorted(READ_CALLS))
def test_default_read_excludes_hidden_notes(loaded, name):
    assert_clean(READ_CALLS[name](loaded))


@pytest.mark.parametrize("name", sorted(READ_CALLS))
@pytest.mark.parametrize("not_true", [False, None, "true", "false", "yes", 1, 0, "True"])
def test_only_a_literal_true_opts_in(loaded, name, not_true):
    assert_clean(READ_CALLS[name](loaded, include_sensitive=not_true))


@pytest.mark.parametrize("name,expect", [
    ("search", {"note-family-weekend-plans", "note-physio-exercises", "decision-boundary-fence-settlement",
                "note-draft-salary-review", "note-cottage-booking"}),
    ("recent", HIDDEN_IDS),
    ("project_context_legal", {"decision-boundary-fence-settlement"}),
    ("project_context_personal", {"note-cottage-booking"}),
    ("what_changed_since", HIDDEN_IDS),
])
def test_opt_in_returns_them(loaded, name, expect):
    """The filter, not an empty database, is what hides them by default."""
    result = READ_CALLS[name](loaded, include_sensitive=True)
    rows = result.get("results") or result.get("nodes")
    assert expect <= {r["id"] for r in rows}


def test_the_title_and_summary_checks_are_not_vacuous(loaded):
    """The strings assert_clean looks for really are in the hidden notes' responses, so a pass
    means they were filtered, not that they were misspelt in the test."""
    text = dump(loaded.recent(limit=20, include_sensitive=True)).lower()
    for title in HIDDEN_TITLES:
        assert title in text, f"{title!r} not found even with the opt-in"
    for fragment in HIDDEN_SUMMARY_FRAGMENTS:
        assert fragment in text, f"{fragment!r} not found even with the opt-in"


def test_default_read_still_returns_normal_notes(loaded):
    ids = {r["id"] for r in loaded.recent(limit=20)["results"]}
    assert ids == NORMAL_IDS
    ids = {r["id"] for r in loaded.what_changed_since("2000-01-01T00:00:00+00:00")["results"]}
    assert ids == NORMAL_IDS


def test_search_finds_the_right_normal_note(loaded):
    top = loaded.search("supplier lead time bedding plants")["results"][0]
    assert top["id"] == "fact-supplier-lead-time-2026-03"


def test_project_context_orders_pillar_then_decision_then_question(loaded):
    nodes = loaded.project_context("garden-centre")["nodes"]
    assert nodes[0]["id"] == "pillar-garden-centre"
    types = [n["type"] for n in nodes]
    assert types.index("decision") < types.index("question") < types.index("concept")


# ---- unlabelled and mislabelled notes are private ---------------------------------------


@pytest.mark.parametrize("node_id", sorted(HIDDEN_IDS))
def test_stored_tiers(loaded, node_id):
    row = loaded.db.conn().execute("SELECT sensitivity FROM memories WHERE id = %s", (node_id,)).fetchone()
    assert row["sensitivity"] == HIDDEN[node_id][0]


def test_unlabelled_note_is_stored_private_and_hidden(loaded):
    row = loaded.db.conn().execute(
        "SELECT sensitivity FROM memories WHERE id = 'note-draft-salary-review'").fetchone()
    assert row["sensitivity"] == loaded.policy.default_for_missing != "normal"
    assert "note-draft-salary-review" not in {r["id"] for r in loaded.recent(limit=20)["results"]}
    assert loaded.get_body("note-draft-salary-review")["error"] == "sensitive node withheld"


def test_a_row_with_an_unknown_tier_is_hidden_by_default(fresh):
    """The default filter is an allow-list, so a tier nobody has heard of is hidden, not shown."""
    fresh.initial_scan()
    store = fresh.store
    store.db.conn().execute("UPDATE memories SET sensitivity = 'public' WHERE id = 'pillar-garden-centre'")
    store.db.conn().execute("UPDATE memories SET sensitivity = '' WHERE id = 'decision-no-peat-compost'")
    ids = {r["id"] for r in store.recent(limit=20)["results"]}
    assert "pillar-garden-centre" not in ids
    assert "decision-no-peat-compost" not in ids
    assert store.get_body("pillar-garden-centre")["error"] == "sensitive node withheld"


def test_the_column_has_no_default(fresh):
    """A raw insert that forgets the label fails loudly instead of defaulting to visible."""
    import psycopg

    fresh.initial_scan()
    with pytest.raises(psycopg.errors.NotNullViolation):
        fresh.store.db.conn().execute(
            "INSERT INTO memories (id, type, title, summary, body, summary_embedding, body_embedding) "
            "VALUES ('x', 'note', 't', 's', 'b', %s::vector, %s::vector)",
            ("[" + ",".join(["0.1"] * DIM) + "]",) * 2,
        )


# ---- get_body ---------------------------------------------------------------------------


@pytest.mark.parametrize("node_id", sorted(HIDDEN_IDS))
def test_get_body_refuses_hidden_notes_and_returns_no_content(loaded, node_id):
    r = loaded.get_body(node_id)
    assert r["error"] == "sensitive node withheld"
    assert r["id"] == node_id
    assert r["sensitivity"] == HIDDEN[node_id][0]
    assert set(r) == {"error", "id", "sensitivity", "hint"}
    text = dump(r).lower()
    for marker in MARKERS:
        assert marker not in text


@pytest.mark.parametrize("not_true", [False, None, "true", "false", 1])
def test_get_body_flag_must_be_literal_true(loaded, not_true):
    assert loaded.get_body("note-physio-exercises", include_sensitive=not_true)["error"]


@pytest.mark.parametrize("node_id", sorted(HIDDEN_IDS))
def test_get_body_with_opt_in_returns_the_text(loaded, node_id):
    r = loaded.get_body(node_id, include_sensitive=True)
    assert HIDDEN[node_id][1] in r["body"]
    assert r["sensitivity"] == HIDDEN[node_id][0]


@pytest.mark.parametrize("node_id", sorted(NORMAL_IDS))
def test_get_body_returns_normal_notes(loaded, node_id):
    r = loaded.get_body(node_id)
    assert "error" not in r
    assert r["body"].strip()


def test_get_body_unknown_id(loaded):
    assert "no node with id" in loaded.get_body("nope")["error"]


# ---- summaries first, bodies only from get_body -----------------------------------------


def test_discovery_tools_never_return_bodies(loaded):
    for name in ("search", "cross_project", "recent", "project_context_legal", "what_changed_since"):
        for flag in (False, True):
            result = READ_CALLS[name](loaded, include_sensitive=flag)
            for row in result.get("results") or result.get("nodes") or []:
                assert "body" not in row, f"{name} returned a body"


def test_summaries_are_one_line_and_short(loaded):
    s = replace(loaded.settings, summary_chars=40)
    tight = MemoryStore(loaded.db, loaded.policy, s, HashEmbedder(DIM))
    for r in tight.recent(limit=20)["results"]:
        assert "\n" not in r["summary"]
        assert len(r["summary"]) <= 40


def test_limits_are_clamped(loaded):
    assert len(loaded.recent(limit=1000)["results"]) <= loaded.settings.max_search_limit
    assert len(loaded.recent(limit=2)["results"]) == 2
    assert len(loaded.recent(limit=0)["results"]) == len(NORMAL_IDS)  # 0 means the default (8)
    assert len(loaded.recent(limit=-5)["results"]) == len(NORMAL_IDS)


# ---- stats ------------------------------------------------------------------------------


def test_stats_default_counts_only_visible_and_reports_one_withheld_number(loaded):
    s = loaded.stats()
    assert s["total"] == len(NORMAL_IDS)
    assert sum(s["by_type"].values()) == len(NORMAL_IDS)
    assert s["withheld"] == len(HIDDEN_IDS)
    assert "by_sensitivity" not in s
    assert {p["project_id"] for p in s["top_projects"]} == {"garden-centre"}


def test_stats_opt_in_breaks_down_tiers(loaded):
    s = loaded.stats(include_sensitive=True)
    assert s["total"] == len(NORMAL_IDS) + len(HIDDEN_IDS)
    assert s["by_sensitivity"] == {"normal": 7, "personal": 5, "legal": 1}
    assert s["withheld"] == 0


# ---- bad input --------------------------------------------------------------------------


def test_bad_arguments_return_errors_not_exceptions(loaded):
    assert "error" in loaded.search("")
    assert "error" in loaded.search("   ")
    assert "error" in loaded.search("x", type="gadget")
    assert "error" in loaded.recent(type="gadget")
    assert "error" in loaded.what_changed_since("yesterday")


def test_naive_timestamp_is_read_as_utc(loaded):
    assert loaded.what_changed_since("2000-01-01T00:00:00")["count"] == len(NORMAL_IDS)
    assert loaded.what_changed_since("2099-01-01T00:00:00+00:00")["count"] == 0


# ---- lock mode --------------------------------------------------------------------------


@pytest.fixture()
def locked(loaded):
    return MemoryStore(loaded.db, loaded.policy, replace(loaded.settings, lock_sensitive=True),
                       HashEmbedder(DIM))


@pytest.mark.parametrize("name", sorted(READ_CALLS))
def test_lock_ignores_the_opt_in_for_every_tool(locked, name):
    result = READ_CALLS[name](locked, include_sensitive=True)
    assert_clean(result)
    assert result["sensitive_locked"] is True


def test_lock_makes_get_body_refuse_even_with_the_flag(locked):
    r = locked.get_body("decision-boundary-fence-settlement", include_sensitive=True)
    assert r["error"] == "sensitive node withheld"
    assert "locked" in r["hint"]
    assert "canary" not in dump(r)


def test_lock_does_not_annotate_ordinary_calls(locked):
    assert "sensitive_locked" not in locked.recent()
