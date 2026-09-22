# TODO

The working backlog is **not** in this file. It lives in the Obsidian vault at
`Projects/campsearch/Roadmap.md`, as checkboxes grouped by area (Search, Home
page, Results page, Campsite page, Data, User accounts, Infra, UI), with the
detail of *how* each item landed in `Projects/campsearch/Log.md`.

Infrastructure and deployment for the pipeline are documented separately, in
`Reference/Guides/Projects/CampSearch.md` and
`Reference/Guides/Services/CampSearch Airflow.md`.

What used to be listed here — clean up the static information, scrape the rest
of it, write a dynamic-info updater — is all done: `scripts/clean_text.py`, six
ingest sources, and `scripts/refresh_dynamic.py` respectively, all wired into
the Airflow DAGs in `dags/`.

Repo-local follow-ups that have no vault entry yet:

- [ ] No tests anywhere in the repo. The fee parser (`clean_text.py`), the
      dynamic WHERE builder (`search.py`) and the source parsers are the
      high-value targets — the scrapers read undocumented JSON APIs that can
      change shape without notice.
- [ ] `open`/`closed` status has no real source — see the dated TODO in
      `scripts/refresh_dynamic.py`. The `is_open` search facet currently runs on
      whatever the fs.usda scrape last wrote, and nothing covers the other five
      sources.
- [ ] `dags/` is committed here but the Airflow host still bind-mounts its own
      copy. Repoint the mount at `repo/dags` so this repo is actually the source
      of truth — see `dags/README.md`.
- [ ] Delete the two empty committed stubs
      (`scripts/findrecareasfornationalparks.py`,
      `scripts/update_static_info_for_park.py`) and the obsolete `notes` file,
      whose column mapping predates 21 migrations.
- [ ] `scrape_californiasbestcamping.py` and `ingest_alltrails.py` are not in
      `run_all.py` or any DAG. Both are deliberate (one-off reconciliation;
      session-scoped MCP auth) — say so in a module docstring so it stops
      reading like an oversight.
