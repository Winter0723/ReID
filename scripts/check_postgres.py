from pathlib import Path
import os
import sys
import time

ROOT_DIR = Path(__file__).resolve().parents[1]
BACKEND_DIR = ROOT_DIR / "FastAPIBackend"
sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_URL = "postgresql+psycopg://myreid:myreid@127.0.0.1:5432/myreid"
DEFAULT_SCENIC_URL = "postgresql+psycopg://myreid:myreid@127.0.0.1:5432/myreid_scenic"
os.environ.setdefault("DATABASE_URL", DEFAULT_URL)
os.environ.setdefault("SCENIC_DATABASE_URL", DEFAULT_SCENIC_URL)

import database
import scenic_database


def main():
    deadline = time.time() + int(os.getenv("MYREID_DB_WAIT_SECONDS", "60"))
    last_error = None
    while time.time() < deadline:
        try:
            database.init_database()
            scenic_database.init_database()
            database.check_database_connection()
            scenic_database.check_database_connection()
            info = database.get_database_info()
            scenic_info = scenic_database.get_database_info()
            print(f"core postgres ok: {info['dialect']}+{info['driver']} {info['url']}")
            print(f"scenic postgres ok: {scenic_info['dialect']}+{scenic_info['driver']} {scenic_info['url']}")
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise SystemExit(f"postgres not ready: {last_error}")


if __name__ == "__main__":
    main()
