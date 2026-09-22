"""Path setup so the tests can import the app modules and the scrape jobs.

`scripts/` is not a package from the test runner's point of view: the jobs are
written to run as `__main__` from inside that directory (that is what
`_pipeline.py`'s own sys.path insert assumes, and how both run_all.py and the
Airflow tasks invoke them). Putting both the repo root and scripts/ on the path
lets a test do `import clean_text` and get the same module the pipeline runs.
"""

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

for path in (ROOT, os.path.join(ROOT, "scripts")):
    if path not in sys.path:
        sys.path.insert(0, path)
