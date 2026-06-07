-- Extensions required by the Skylize schema.
-- Run at container init (superuser) before `alembic upgrade head` applies tables.
CREATE EXTENSION IF NOT EXISTS "pgcrypto";    -- gen_random_uuid()
CREATE EXTENSION IF NOT EXISTS "pg_trgm";      -- trigram fuzzy / FTS support
CREATE EXTENSION IF NOT EXISTS "btree_gin";    -- composite GIN indexes
