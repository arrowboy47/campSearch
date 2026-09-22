# Deploying the CampSearch Flask app

The app runs under gunicorn in a container, bound to `127.0.0.1:8095` on the
host. It is not published directly — put [Nginx Proxy Manager] in front of it
when it needs to be reachable.

## Prerequisites

`campsearch-pg` must already be up, because this compose file joins its network
(`campsearch-pg_default`, declared `external: true`) and reaches Postgres at
`campsearch-pg:5432`. That is the same route the Airflow scheduler takes, and
it is why `CAMPSEARCH_DATABASE_URL` here names the container rather than the
`127.0.0.1:5434` tunnel a laptop uses.

## First run

```bash
cd deploy/app
cp .env.example .env && chmod 600 .env    # fill in all four values
docker compose build
docker compose up -d
docker compose ps                          # wait for "healthy"
curl -fsS http://127.0.0.1:8095/healthz    # {"status":"ok","database":"ok"}
```

`SECRET_KEY` is not optional in production. `config.py` falls back to a fixed
dev string when it is unset, which signs every session with a value that is in
the public repo and resets logins on each restart.

## Deploying a change

```bash
git pull
docker compose build && docker compose up -d
```

There is no read-only repo mount here, unlike the Airflow deployment — the code
is baked into the image, so a code change means a rebuild.

## Health

`/healthz` opens a database connection and runs `SELECT 1` rather than just
returning 200. A process that is up but cannot reach Postgres serves 500s on
every real page, and a check that only proved the port was open would call that
healthy. It returns 503 with the exception *type* (never the DSN) when the
database is unreachable.

Point the [Uptime Kuma] monitor at `/healthz`, not `/`.

## State

The only writable state is avatar uploads, in the `campsearch_uploads` named
volume mounted at `/app/static/uploads`. Everything else in the image is
disposable. Back that volume up with the database, or accept that profile
pictures are lost on a rebuild.

## Known gap: there is no bootstrap path for an empty database

The migration chain starts from the restored laptop dump, not from an empty
Postgres — `db/migrations/0001_baseline.sql` assumes those tables already
exist, and `schema.sql` is an older artifact that does not match (applying it
and then running `migrate.py` fails at 0002 on a missing `overview` column).

So this compose file can deploy the app against the *existing* database, but
the repo cannot currently stand up a new one from scratch. Writing a true
baseline is tracked in the Roadmap.

[Nginx Proxy Manager]: ../../README.md
[Uptime Kuma]: ../../README.md
