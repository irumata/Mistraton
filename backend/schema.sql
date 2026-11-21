CREATE TABLE IF NOT EXISTS mistralethon.schema_migrations (
    version integer PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS mistralethon.app_meta (
    id boolean PRIMARY KEY DEFAULT TRUE CHECK (id),
    name text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO mistralethon.app_meta (id, name)
VALUES (TRUE, 'Mistralethon') ON CONFLICT (id) DO NOTHING;

INSERT INTO mistralethon.schema_migrations (version)
VALUES (1) ON CONFLICT (version) DO NOTHING;
