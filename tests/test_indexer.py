from __future__ import annotations

import logging
import os
import time

import pytest

from conftest import HIDDEN_IDS, NORMAL_IDS
from hushbrain.indexer import ScanResult

pytestmark = pytest.mark.db


def recent_ids(store):
    return {r["id"] for r in store.recent(limit=20)["results"]}


def edit(vault, name, old, new):
    p = vault / "nodes" / f"{name}.md"
    p.write_text(p.read_text().replace(old, new, 1))
    return p


def test_initial_scan_indexes_every_note_and_hides_the_right_ones(fresh):
    res = fresh.initial_scan()
    assert res == ScanResult(files=13, embedded=13, skipped_embeds=0, failed=0)
    assert recent_ids(fresh.store) == NORMAL_IDS


def test_second_scan_skips_embeddings(fresh):
    fresh.initial_scan()
    res = fresh.initial_scan()
    assert (res.embedded, res.skipped_embeds, res.failed) == (0, 13, 0)
    assert recent_ids(fresh.store) == NORMAL_IDS


def test_relabelling_a_note_takes_effect_without_re_embedding(fresh):
    """A re-label is a frontmatter-only edit: the content hash is unchanged. If the cheap path
    forgot to write the tier, the note would stay visible after being marked private."""
    fresh.initial_scan()
    embedded_before = fresh.embedded
    p = edit(fresh.vault, "decision-no-peat-compost", "sensitivity: normal", "sensitivity: personal")
    assert fresh.index_file(p) == "refreshed"
    assert fresh.embedded == embedded_before
    assert "decision-no-peat-compost" not in recent_ids(fresh.store)
    assert fresh.store.get_body("decision-no-peat-compost")["error"] == "sensitive node withheld"


def test_removing_the_label_makes_a_visible_note_private(fresh):
    fresh.initial_scan()
    p = edit(fresh.vault, "concept-seasonal-stock-rotation", "sensitivity: normal\n", "")
    fresh.index_file(p)
    assert "concept-seasonal-stock-rotation" not in recent_ids(fresh.store)


def test_a_label_can_be_lowered_by_the_author_but_not_below_a_project_floor(fresh):
    fresh.initial_scan()
    p = edit(fresh.vault, "note-physio-exercises", "sensitivity: personal", "sensitivity: normal")
    fresh.index_file(p)
    assert "note-physio-exercises" in recent_ids(fresh.store)
    p = edit(fresh.vault, "note-cottage-booking", "tags: [example]", "tags: [example, again]")
    fresh.index_file(p)
    assert "note-cottage-booking" not in recent_ids(fresh.store)


def test_deleting_a_file_removes_its_row(fresh):
    fresh.initial_scan()
    p = fresh.vault / "nodes" / "note-physio-exercises.md"
    p.unlink()
    assert fresh.index_file(p) == "deleted"
    ids = {r["id"] for r in fresh.store.recent(limit=20, include_sensitive=True)["results"]}
    assert "note-physio-exercises" not in ids


def test_deleting_a_file_whose_id_differs_from_its_name_removes_its_row(fresh):
    nodes = fresh.vault / "nodes"
    p = nodes / "renamed-on-disk.md"
    p.write_text("---\nid: a-different-id\nsensitivity: personal\nsummary: A hidden note.\n---\nBody.\n")
    fresh.index_file(p)
    assert fresh.store.get_body("a-different-id", include_sensitive=True)["body"].strip() == "Body."
    p.unlink()
    fresh.index_file(p)
    assert "error" in fresh.store.get_body("a-different-id", include_sensitive=True)


def test_changing_a_files_id_drops_the_old_row(fresh):
    p = fresh.vault / "nodes" / "moving-id.md"
    p.write_text("---\nid: old-id\nsensitivity: normal\nsummary: Old.\n---\nBody.\n")
    fresh.index_file(p)
    p.write_text("---\nid: new-id\nsensitivity: normal\nsummary: Old.\n---\nBody.\n")
    fresh.index_file(p)
    assert "error" in fresh.store.get_body("old-id", include_sensitive=True)
    assert "error" not in fresh.store.get_body("new-id")


def test_a_broken_file_is_skipped_and_the_scan_continues(fresh):
    (fresh.vault / "nodes" / "broken.md").write_text("---\nkey: [unclosed\n---\nbody\n")
    res = fresh.initial_scan()
    assert (res.embedded, res.failed) == (13, 1)


def test_reconcile_catches_a_change_the_watcher_never_reported(fresh, caplog):
    fresh.initial_scan()
    # Only the edited file may look newer than the last sweep, whatever mtimes a checkout gave.
    fresh._last_reconcile = time.time()
    p = edit(fresh.vault, "question-should-we-open-sundays", "sensitivity: normal", "sensitivity: legal")
    os.utime(p, (time.time() + 5, time.time() + 5))
    with caplog.at_level(logging.WARNING, logger="hushbrain.indexer"):
        missed = fresh.reconcile()
    assert missed == [p]
    assert "WATCHER MISS" in caplog.text
    assert fresh.drain() == 1
    assert "question-should-we-open-sundays" not in recent_ids(fresh.store)


def test_reconcile_removes_rows_for_files_it_saw_disappear(fresh):
    fresh.initial_scan()
    fresh.reconcile()                       # first sweep records which files exist
    (fresh.vault / "nodes" / "note-physio-exercises.md").unlink()
    fresh.reconcile()
    ids = {r["id"] for r in fresh.store.recent(limit=20, include_sensitive=True)["results"]}
    assert "note-physio-exercises" not in ids
    assert len(ids) == 12


def test_logs_never_name_a_hidden_note(fresh, caplog):
    with caplog.at_level(logging.DEBUG):
        fresh.initial_scan()
        fresh.index_file(fresh.vault / "nodes" / "note-physio-exercises.md")
        (fresh.vault / "nodes" / "note-family-weekend-plans.md").unlink()
        fresh.index_file(fresh.vault / "nodes" / "note-family-weekend-plans.md")
    log_text = caplog.text.lower()
    for hid in HIDDEN_IDS:
        assert hid not in log_text
    assert "canary" not in log_text
    assert "pillar-garden-centre" in log_text  # normal notes are still named
    assert "no valid sensitivity label" in log_text  # the unlabelled and misspelt ones are flagged
