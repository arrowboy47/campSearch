-- 0025_admin_and_moderation
-- Admin flag on users and audit trail for moderation actions.
--
--   is_admin            - flag to identify admin users who can moderate content.
--   moderation_actions  - immutable audit log of every admin action on reports
--                         and reviews. Uses ON DELETE SET NULL to preserve the
--                         audit record when an admin account is deleted; audit
--                         entries must outlive the accounts that performed them.
--   content_reports     - user-submitted reports of problematic reviews or photos.
--                         Reporters can be anonymous (reporter_id NULL). Uses
--                         ON DELETE SET NULL to preserve the report when the
--                         reporter account is deleted.
--
-- The CHECK constraints are declared inline to ensure idempotency. Postgres has no
-- ADD CONSTRAINT IF NOT EXISTS, so a separate ALTER would fail on a second run.

ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS users_admin_idx ON users (id) WHERE is_admin;

CREATE TABLE IF NOT EXISTS moderation_actions (
    id           BIGSERIAL PRIMARY KEY,
    admin_id     INTEGER NULL REFERENCES users (id) ON DELETE SET NULL,
    action       TEXT NOT NULL,
    target_type  TEXT NOT NULL,
    target_id    BIGINT NOT NULL,
    reason       TEXT,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT moderation_actions_action_check CHECK (
        action IN ('approve', 'reject', 'remove', 'restore', 'dismiss')
    ),
    CONSTRAINT moderation_actions_target_type_check CHECK (
        target_type IN ('review', 'review_photo', 'attribute_report')
    )
);

CREATE INDEX IF NOT EXISTS moderation_actions_target_idx ON moderation_actions (target_type, target_id);
CREATE INDEX IF NOT EXISTS moderation_actions_admin_created_idx ON moderation_actions (admin_id, created_at DESC);

CREATE TABLE IF NOT EXISTS content_reports (
    id           BIGSERIAL PRIMARY KEY,
    reporter_id  INTEGER NULL REFERENCES users (id) ON DELETE SET NULL,
    target_type  TEXT NOT NULL,
    target_id    BIGINT NOT NULL,
    reason       TEXT,
    status       TEXT NOT NULL DEFAULT 'open',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT content_reports_target_type_check CHECK (
        target_type IN ('review', 'review_photo')
    ),
    CONSTRAINT content_reports_status_check CHECK (
        status IN ('open', 'actioned', 'dismissed')
    )
);

CREATE INDEX IF NOT EXISTS content_reports_status_idx ON content_reports (status, created_at DESC);
