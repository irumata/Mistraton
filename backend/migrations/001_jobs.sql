CREATE TABLE jobs (
    id uuid PRIMARY KEY,
    text text NOT NULL,
    status text NOT NULL CHECK (status IN ('queued', 'running', 'finished', 'failed', 'enqueue_failed')),
    result text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX jobs_created_at_idx ON jobs (created_at);
