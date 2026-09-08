# campsearch-pg

Postgres 16 for CampSearch. Runs on **thebigbox** (`glassslipper@192.168.1.178`),
bound to `127.0.0.1:5434` only — never exposed on the LAN. Reached from a dev box
over an SSH tunnel, same model as the Personal Data Warehouse DB.

## First deploy

```bash
# 1. password -> Vaultwarden entry "campsearch-pg"
PW=$(openssl rand -base64 24)

# 2. copy the stack up
ssh thebigbox 'mkdir -p /media/containers/campsearch-pg'
scp deploy/campsearch-pg/docker-compose.yml thebigbox:/media/containers/campsearch-pg/
ssh thebigbox "printf 'CAMPSEARCH_PG_PASSWORD=%s\n' '$PW' > /media/containers/campsearch-pg/.env && chmod 600 /media/containers/campsearch-pg/.env"

# 3. bring it up (boots an empty `camping` db)
ssh thebigbox 'cd /media/containers/campsearch-pg && docker compose up -d'

# 4. verify: Up, and nothing on a public interface
ssh thebigbox 'docker ps --filter name=campsearch-pg --format "{{.Names}} {{.Status}}"'
ssh thebigbox 'sudo ss -tlnp | grep 5434'   # 127.0.0.1:5434 only
```

## Load data + migrate (from the dev box)

```bash
# keep this tunnel open in another shell
ssh -L 5434:localhost:5434 thebigbox

export DATABASE_URL="postgresql://campsearch:$PW@localhost:5434/camping"

# restore the recovered laptop dump
pg_restore --no-owner --no-privileges --clean --if-exists \
  -d "$DATABASE_URL" /home/Jadu/temp/camping.dump

# bring the restored schema up to v2
python scripts/migrate.py
python scripts/migrate.py --status   # all applied
```

If `pg_restore` / `psql` are not installed locally: `sudo dnf install postgresql`
(client only), or run the restore inside the container after `docker cp camping.dump
campsearch-pg:/tmp/`.

## Backups

`pg_dump` on a cron on thebigbox (see the vault note `Guides/Projects/CampSearch.md`).
