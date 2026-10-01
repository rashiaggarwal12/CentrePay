"""A local PostgreSQL for development and tests: no installer, no Docker, no password.

    python scripts/local_postgres.py           # start (if needed) and print the URL
    python scripts/local_postgres.py status
    python scripts/local_postgres.py stop
    python scripts/local_postgres.py url       # print the URL without starting

It runs Postgres's own `pg_ctl` with the binaries bundled in the `pgserver` package,
on a FIXED port (54329, override with CENTREPAY_PG_PORT), so DATABASE_URL in .env stays
valid across restarts:

    DATABASE_URL=postgresql://postgres@127.0.0.1:54329/centrepay_dev

The server log lives OUTSIDE the data directory. (pgserver's own launcher keeps it inside,
and after an unclean shutdown such as a PC restart, Postgres's recovery tries to fsync every
file in the data directory, including that open log, which Windows refuses with a
"sharing violation", stalling startup.)

Data: %LOCALAPPDATA%/centrepay-pg     Log: %LOCALAPPDATA%/centrepay-pg-logs/postgres.log
"""

# Dev tool: runs only Postgres's own bundled binaries, with constant arguments.
# ruff: noqa: S603, S608
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pgserver
import tzdata

HOME = Path(os.environ.get("LOCALAPPDATA", Path.home()))
DATA = HOME / "centrepay-pg"
LOGS = HOME / "centrepay-pg-logs"
PORT = int(os.environ.get("CENTREPAY_PG_PORT", "54329"))
DEV_DB = "centrepay_dev"

INSTALL = Path(pgserver.__file__).parent / "pginstall"
BIN = INSTALL / "bin"


def exe(name: str) -> str:
    return str(BIN / (f"{name}.exe" if os.name == "nt" else name))


def url(db: str = "postgres") -> str:
    return f"postgresql://postgres@127.0.0.1:{PORT}/{db}"


def ensure_timezone_data() -> None:
    # The Windows build of pgserver ships without Postgres' timezone files, so even
    # `SET TIME ZONE 'UTC'` (which Django does on connect) fails. tzdata has the same files.
    target = INSTALL / "share" / "postgresql" / "timezone"
    if not target.exists():
        shutil.copytree(Path(tzdata.__file__).parent / "zoneinfo", target)


def is_running() -> bool:
    if not (DATA / "PG_VERSION").exists():
        return False
    return (
        subprocess.run([exe("pg_ctl"), "status", "-D", str(DATA)], capture_output=True).returncode
        == 0
    )


def init_cluster() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            exe("initdb"),
            "-D",
            str(DATA),
            "-U",
            "postgres",
            "--auth=trust",
            "--encoding=UTF8",
            "--locale=C",
        ],
        check=True,
        capture_output=True,
    )


def start() -> None:
    ensure_timezone_data()
    if not (DATA / "PG_VERSION").exists():
        init_cluster()
    if is_running():
        return
    LOGS.mkdir(parents=True, exist_ok=True)
    stale_log = DATA / "log"  # left by the old pgserver launcher; keep it, out of the way
    if stale_log.exists():
        stale_log.replace(LOGS / "pgserver-old.log")
    # No pipes here: the server process inherits pg_ctl's handles, so capturing output
    # would wait forever on Windows. The log file captures everything instead.
    result = subprocess.run(
        [
            exe("pg_ctl"),
            "start",
            "-D",
            str(DATA),
            "-l",
            str(LOGS / "postgres.log"),
            "-w",
            "-t",
            "120",
            "-o",
            f"-p {PORT} -c listen_addresses=127.0.0.1 -c unix_socket_directories=",
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        sys.exit(f"Postgres failed to start; see {LOGS / 'postgres.log'}")
    ensure_dev_database()


def ensure_dev_database() -> None:
    exists = subprocess.run(
        [
            exe("psql"),
            "-h",
            "127.0.0.1",
            "-p",
            str(PORT),
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-tAc",
            f"SELECT 1 FROM pg_database WHERE datname = '{DEV_DB}'",
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if exists != "1":
        subprocess.run(
            [exe("createdb"), "-h", "127.0.0.1", "-p", str(PORT), "-U", "postgres", DEV_DB],
            check=True,
        )


def stop() -> None:
    if is_running():
        subprocess.run([exe("pg_ctl"), "stop", "-D", str(DATA), "-m", "fast", "-w"], check=True)


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else "start"
    if command == "start":
        start()
        print(url())
    elif command == "stop":
        stop()
        print("stopped")
    elif command == "status":
        print(f"running on port {PORT}" if is_running() else "stopped")
    elif command == "url":
        print(url())
    else:
        sys.exit(__doc__)
