import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path


DB_PATH = Path(os.environ.get("SERE_DB_PATH", Path(__file__).resolve().parent / "sere.db"))


@contextmanager
def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()
