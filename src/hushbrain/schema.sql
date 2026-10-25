-- hushbrain schema. {dim} is filled in from the embed_dim setting at init time.
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS memories (
    id                text PRIMARY KEY,
    type              text NOT NULL,
    project_id        text,
    title             text NOT NULL,
    summary           text NOT NULL,
    body              text NOT NULL,
    -- No default, on purpose: every writer must state a tier. The indexer stamps unlabelled
    -- notes with the policy's fail-closed tier before they get here.
    sensitivity       text NOT NULL,
    summary_embedding vector({dim}) NOT NULL,
    body_embedding    vector({dim}) NOT NULL,
    content_hash      text,
    -- The file the row came from, so a deleted file removes its row even when the
    -- frontmatter id differs from the filename.
    source_file       text,
    created_at        timestamptz NOT NULL DEFAULT now(),
    updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS memories_updated_at_idx  ON memories (updated_at DESC);
CREATE INDEX IF NOT EXISTS memories_project_idx     ON memories (project_id);
CREATE INDEX IF NOT EXISTS memories_sensitivity_idx ON memories (sensitivity);
CREATE INDEX IF NOT EXISTS memories_source_file_idx ON memories (source_file);
CREATE INDEX IF NOT EXISTS memories_summary_hnsw_idx
    ON memories USING hnsw (summary_embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS hushbrain_meta (
    key   text PRIMARY KEY,
    value text NOT NULL
);
