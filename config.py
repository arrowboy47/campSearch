"""Central config. Reads from environment (and a local .env if present).

Nothing secret lives in the repo anymore — see .env.example for the keys.
"""

import os

from dotenv import load_dotenv

load_dotenv()


def _require(name):
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(f"{name} is not set (copy .env.example to .env)")
    return val


# Postgres connection string, e.g.
#   postgresql://campsearch:pw@localhost:5434/camping
DATABASE_URL = os.environ.get("DATABASE_URL")

# Third-party API keys. Not required just to import this module, so scripts that
# don't need them can still run; call the getters where they're actually used.
OPENWEATHER_API_KEY = os.environ.get("OPENWEATHER_API_KEY")
RIDB_API_KEY = os.environ.get("RIDB_API_KEY")


def database_url():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set (copy .env.example to .env)")
    return DATABASE_URL


def openweather_api_key():
    return _require("OPENWEATHER_API_KEY")


def ridb_api_key():
    return _require("RIDB_API_KEY")
