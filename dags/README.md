# Airflow DAGs

The three DAGs that orchestrate the scrape/refresh pipeline, plus the shared
helper they all build tasks with.

| File | DAG | Schedule (America/Los_Angeles) |
|---|---|---|
| `campsearch_refresh_daily.py` | `campsearch_refresh_daily` | 03:20 daily |
| `campsearch_refresh_weekly.py` | `campsearch_refresh_weekly` | Sun 04:10 |
| `campsearch_trails_monthly.py` | `campsearch_trails_monthly` | 1st, 05:00 |
| `campsearch_common.py` | — | shared `campsearch_task()`, default args, failure callback |

Each task shells out to `scripts/<job>.py` with `BashOperator` rather than
importing it: the scrapers run in their own venv (`/opt/campsearch-venv`, built
from `requirements.txt`) while Airflow uses the image's environment, so an
Airflow upgrade can never break a scraper. Tasks `cd` into `scripts/` and run
the job as `__main__`, exactly as `run_all.py` does, which is what keeps
`_pipeline.py`'s `sys.path` insert working.

No secrets here. `DATABASE_URL` and the API keys are Airflow Variables rendered
per task instance as `{{ var.value.* }}`, so a DAG parse does not touch them.

## Deploying

> **These files are not yet the deployed copy.** The Airflow host bind-mounts
> `/media/prod/campsearch/dags`, which is a separate directory from the
> `repo/` checkout on the same host. A change committed here does **not** reach
> the scheduler until that mount is repointed at `repo/dags` (or the files are
> copied across by hand). As of this commit the two are byte-identical —
> verify with `md5sum` before assuming it, and repoint the mount so this stops
> being a manual sync.

The deployment itself (compose file, Dockerfile, operational runbook) lives on
the host at `/media/prod/campsearch` and is documented in the vault under
`Reference/Guides/Services/CampSearch Airflow.md`. Deploying a scraper change
is `git -C repo pull` on the host; only a `requirements.txt` change needs a
rebuild.
