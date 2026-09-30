"""Start a throwaway local Postgres (no Docker, no install) and print its DATABASE_URL.

    pip install pgserver tzdata
    python scripts/local_postgres.py
    # then, in the same shell:
    #   export DATABASE_URL=<printed url>     (PowerShell: $env:DATABASE_URL="<url>")
    #   pytest

Data lives in %LOCALAPPDATA%/centrepay-pg (or ~/centrepay-pg) and the server keeps
running in the background until you run this script with --stop.
"""

import os
import shutil
import sys
from pathlib import Path

import pgserver
import tzdata

# pgserver's Windows build ships without Postgres' timezone database, so even
# `SET TIME ZONE 'UTC'` (which Django does on connect) fails. The tzdata package has
# the same IANA zone files; copy them in once.
tz_target = Path(pgserver.__file__).parent / "pginstall" / "share" / "postgresql" / "timezone"
if not tz_target.exists():
    shutil.copytree(Path(tzdata.__file__).parent / "zoneinfo", tz_target)

data_dir = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "centrepay-pg"
server = pgserver.get_server(data_dir, cleanup_mode=None)

if "--stop" in sys.argv:
    server.cleanup()
    print("stopped")
else:
    print(server.get_uri())
