"""Postgres connection and schema management."""
from __future__ import annotations

from importlib import resources

import psycopg
from psycopg.rows import dict_row

SCHEMA_VERSION = "1"


class SchemaError(RuntimeError):
    pass


class Database:
    """One lazy, persistent autocommit connection that reconnects if it drops."""

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._conn: psycopg.Connection | None = None

    def conn(self) -> psycopg.Connection:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self.dsn, row_factory=dict_row, autocommit=True)
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None


def init_schema(db: Database, dim: int) -> None:
    """Create the tables if absent. Refuses to change the embedding dimension of an existing store."""
    dim = int(dim)
    sql = resources.files("hushbrain").joinpath("schema.sql").read_text(encoding="utf-8")
    conn = db.conn()
    conn.execute(sql.replace("{dim}", str(dim)))
    row = conn.execute("SELECT value FROM hushbrain_meta WHERE key = 'embed_dim'").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO hushbrain_meta (key, value) VALUES ('schema_version', %s), ('embed_dim', %s)",
            (SCHEMA_VERSION, str(dim)),
        )
    elif int(row["value"]) != dim:
        raise SchemaError(
            f"this store was created with embed_dim={row['value']} but the setting is {dim}; "
            "changing the dimension means re-embedding into a new store"
        )


def verify_schema(db: Database, dim: int) -> None:
    """Fail fast, with a message that says what to do, if the store is missing or mismatched."""
    try:
        row = db.conn().execute("SELECT value FROM hushbrain_meta WHERE key = 'embed_dim'").fetchone()
    except psycopg.errors.UndefinedTable as e:
        raise SchemaError("the store is not initialised; run `hushbrain init-db`") from e
    if row is None:
        raise SchemaError("the store has no metadata; run `hushbrain init-db`")
    if int(row["value"]) != int(dim):
        raise SchemaError(f"store embed_dim is {row['value']} but the setting is {dim}")
