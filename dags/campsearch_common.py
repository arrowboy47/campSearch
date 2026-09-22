"""Shared plumbing for the CampSearch DAGs.

Design notes, because they are the non-obvious part:

* **Tasks shell out, they do not import.** The scrapers live in their own venv
  (`/opt/campsearch-venv`) with their own pins. Airflow pins hard too. Keeping
  them in separate interpreters means an Airflow upgrade can never break a
  scraper, and vice versa. `_pipeline.py` also puts the repo root on `sys.path`
  itself, which only works when the script is run as `__main__` from
  `scripts/` — so that is exactly how the tasks invoke it.

* **Secrets come from Airflow Variables, rendered at runtime.** They are
  templated into the task env as `{{ var.value.* }}` rather than read with
  `Variable.get()` at module scope — module scope runs on every DAG parse, which
  would hammer the metadata DB every 60s for values that only matter when a task
  actually runs.

* **`catchup=False` everywhere, deliberately.** These jobs scrape *current*
  state — what campgrounds exist right now, what the weather will be. There is
  no such thing as "the fs.usda.gov listing as of three Sundays ago", so
  replaying missed intervals would just re-scrape today's data N times and burn
  the API budget. The daily/weekly cadence is a freshness policy, not a
  partitioning scheme. (This is the one place CampSearch differs from a
  card-aggregate pipeline, where backfill is the whole point.)
"""

from __future__ import annotations

import logging
import pendulum
from airflow.models import Variable
from airflow.providers.standard.operators.bash import BashOperator

log = logging.getLogger(__name__)

CAMPSEARCH_DIR = "/opt/campsearch"
VENV_PYTHON = "/opt/campsearch-venv/bin/python"
TZ = pendulum.timezone("America/Los_Angeles")

# Rendered per task instance, not at parse time.
TASK_ENV = {
    "DATABASE_URL": "{{ var.value.campsearch_database_url }}",
    "OPENWEATHER_API_KEY": "{{ var.value.campsearch_openweather_key }}",
    "RIDB_API_KEY": "{{ var.value.campsearch_ridb_key }}",
    "PYTHONUNBUFFERED": "1",
}

DEFAULT_ARGS = {
    "owner": "aiden",
    "retries": 2,
    "retry_delay": pendulum.duration(minutes=10),
    "retry_exponential_backoff": True,
    "max_retry_delay": pendulum.duration(minutes=45),
    "depends_on_past": False,
}


def notify_failure(context) -> None:
    """Telegram on task failure. Never raises — a broken alert must not also
    break the callback and mask the original error."""
    try:
        token = Variable.get("campsearch_telegram_bot_token", default_var=None)
        chat_id = Variable.get("campsearch_telegram_chat_id", default_var=None)
        ti = context.get("task_instance")
        dag_id = getattr(ti, "dag_id", "?")
        task_id = getattr(ti, "task_id", "?")
        exc = context.get("exception")
        msg = (
            f"CampSearch Airflow FAILED\n"
            f"dag: {dag_id}\ntask: {task_id}\n"
            f"try: {getattr(ti, 'try_number', '?')}\n"
            f"error: {str(exc)[:400]}"
        )
        if not token or not chat_id:
            log.warning("Telegram not configured; failure was: %s", msg)
            return
        import requests

        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": msg},
            timeout=15,
        )
    except Exception:  # noqa: BLE001
        log.exception("failure notification itself failed; swallowing")


def campsearch_task(
    task_id: str,
    script: str,
    args: str = "",
    timeout_minutes: int = 120,
    pool: str | None = None,
) -> BashOperator:
    """One scraper script as one task.

    `set -o pipefail` + `cd` into scripts/ so `_pipeline`'s sys.path trick and
    the sibling imports (`import weather`, `import config`) resolve the same way
    they do under run_all.py.
    """
    return BashOperator(
        task_id=task_id,
        bash_command=(
            f"set -euo pipefail\n"
            f"cd {CAMPSEARCH_DIR}/scripts\n"
            f"exec {VENV_PYTHON} {script} {args}".rstrip()
        ),
        env=TASK_ENV,
        append_env=True,
        execution_timeout=pendulum.duration(minutes=timeout_minutes),
        on_failure_callback=notify_failure,
        pool=pool or "default_pool",
        doc_md=f"Runs `scripts/{script} {args}`. Writes its own `scrape_runs` row.",
    )
