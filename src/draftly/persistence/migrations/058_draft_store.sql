CREATE TABLE IF NOT EXISTS draft_revisions (
    id            text PRIMARY KEY,
    run_id        text NOT NULL,
    org_id        text NOT NULL,
    generation    integer NOT NULL,
    path          text NOT NULL,
    action        text NOT NULL,
    sealed        boolean NOT NULL DEFAULT FALSE,
    content_size  integer NOT NULL DEFAULT 0,
    created_at    timestamptz NOT NULL DEFAULT now(),
    sealed_at     timestamptz
);
CREATE INDEX IF NOT EXISTS draft_revisions_run_gen
    ON draft_revisions (run_id, generation);

CREATE TABLE IF NOT EXISTS draft_chunks (
    draft_id      text NOT NULL REFERENCES draft_revisions (id) ON DELETE CASCADE,
    chunk_index   integer NOT NULL,
    content       text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (draft_id, chunk_index)
);