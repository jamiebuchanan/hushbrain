"""Shared fixtures. Database tests need Postgres with pgvector.

Set HUSHBRAIN_TEST_DSN to any server where the user may CREATE DATABASE, or run
scripts/test.sh, which starts a throwaway container. Each fixture makes its own database
and drops it afterwards, so tests never touch anything you already have.
"""
from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest
from psycopg.conninfo import make_conninfo

from hushbrain.config import Settings
from hushbrain.db import Database, init_schema
from hushbrain.embeddings import HashEmbedder
from hushbrain.indexer import Indexer
from hushbrain.policy import Policy, load
from hushbrain.store import MemoryStore

ROOT = Path(__file__).resolve().parent.parent
SAMPLE_VAULT = ROOT / "sample-vault"
EXAMPLE_POLICY = ROOT / "examples" / "policy.example.toml"
DIM = 64

# What the policy must do to each sample note. Written out by hand, not derived from the
# code under test, so a bug in the parser cannot hide itself.
NORMAL_IDS = {
    "pillar-garden-centre",
    "decision-no-peat-compost",
    "concept-seasonal-stock-rotation",
    "playbook-spring-opening-checklist",
    "fact-supplier-lead-time-2026-03",
    "source-trade-body-pricing-survey",
    "question-should-we-open-sundays",
}
HIDDEN = {  # id -> (stored tier, marker text planted in the note)
    "note-family-weekend-plans": ("personal", "canary-personal-weekend"),
    "note-physio-exercises": ("personal", "canary-personal-physio"),
    "decision-boundary-fence-settlement": ("legal", "canary-legal-fence"),
    "note-draft-salary-review": ("personal", "canary-unlabelled-salary"),  # no label at all
    "note-persnal-typo": ("personal", "canary-typo-label"),                # misspelt label
    "note-cottage-booking": ("personal", "canary-floor-cottage"),          # label normal, project floor
}
HIDDEN_IDS = set(HIDDEN)
MARKERS = {m for _, m in HIDDEN.values()}
# Titles and summaries of the hidden notes, also written out by hand from the sample vault.
# A response can leak a note without naming its id, so the leak checks look for these too.
HIDDEN_TITLES = {
    "family weekend plans",
    "physio exercises for the knee",
    "boundary fence settlement terms",
    "draft thoughts on a salary review",
    "a note with a misspelt label",
    "cottage booking for the summer holiday",
}
HIDDEN_SUMMARY_FRAGMENTS = {
    "arrangements for a family weekend away",
    "knee exercises and appointment dates",
    "agreed terms of a boundary fence dispute",
    "draft about pay expectations",
    "sensitivity label is misspelt",
    "holiday cottage, labelled normal",
}


def _admin_dsn() -> str | None:
    return os.environ.get("HUSHBRAIN_TEST_DSN")


@pytest.fixture(scope="session")
def policy() -> Policy:
    return load(EXAMPLE_POLICY)


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(embedder="hash", embed_dim=DIM, policy_path=EXAMPLE_POLICY, debounce_ms=0)


def _make_database(settings: Settings):
    admin = _admin_dsn()
    if not admin:
        pytest.skip("HUSHBRAIN_TEST_DSN is not set (run scripts/test.sh)")
    name = "hb_test_" + uuid.uuid4().hex[:12]
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{name}"')
    dsn = make_conninfo(admin, dbname=name)
    db = Database(dsn)
    init_schema(db, settings.embed_dim)
    return db, dsn, name, admin


def _drop(db: Database, name: str, admin: str) -> None:
    db.close()
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')


def _index_sample(store: MemoryStore, settings: Settings, policy: Policy, vault: Path) -> Indexer:
    s = replace(settings, vault_dir=vault)
    idx = Indexer(s, policy, store, HashEmbedder(DIM))
    idx.initial_scan()
    return idx


@pytest.fixture(scope="session")
def loaded(settings, policy):
    """A database holding the whole sample vault. Read-only: do not write to it in a test."""
    db, dsn, name, admin = _make_database(settings)
    store = MemoryStore(db, policy, settings, HashEmbedder(DIM))
    _index_sample(store, settings, policy, SAMPLE_VAULT)
    store.dsn = dsn  # type: ignore[attr-defined]
    yield store
    _drop(db, name, admin)


@pytest.fixture()
def fresh(settings, policy, tmp_path):
    """An empty database plus a writable copy of the sample vault, for tests that change things."""
    db, dsn, name, admin = _make_database(settings)
    vault = tmp_path / "vault"
    shutil.copytree(SAMPLE_VAULT, vault)
    store = MemoryStore(db, policy, replace(settings, vault_dir=vault), HashEmbedder(DIM))
    idx = Indexer(replace(settings, vault_dir=vault), policy, store, HashEmbedder(DIM))
    idx.dsn = dsn  # type: ignore[attr-defined]
    idx.vault = vault  # type: ignore[attr-defined]
    yield idx
    _drop(db, name, admin)
