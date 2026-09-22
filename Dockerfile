# CampSearch Flask app.
#
# Runs under gunicorn, not `flask run` -- the dev server is single-threaded and
# explicitly not for production. `app.run(debug=True)` at the bottom of app.py
# stays for local work; it is never reached here, because gunicorn imports the
# module and takes `app` directly.

FROM python:3.12-slim

# psycopg2-binary ships its own libpq, so no build toolchain is needed. curl is
# here for the healthcheck only.
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Requirements first so a code change does not re-resolve the dependency tree.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt gunicorn>=21.2

COPY . .

# Avatar uploads are written at runtime; the volume mounts over this, but the
# directory has to exist and be writable for the case where it does not.
RUN mkdir -p static/uploads/avatars \
    && useradd --create-home --uid 1000 campsearch \
    && chown -R campsearch:campsearch /app
USER campsearch

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/healthz || exit 1

# Two workers, four threads: this is an I/O-bound app (Postgres, OpenWeather,
# Nominatim, OSRM) on a small box, so threads buy more than processes do.
# --timeout 60 covers the geocode/OSRM calls on the campsite page.
CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "--threads", "4", \
     "--timeout", "60", "--access-logfile", "-", "app:app"]
