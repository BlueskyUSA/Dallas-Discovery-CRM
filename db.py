"""
Postgres connection layer.

This intentionally provides a thin sqlite3-compatible shim over psycopg
(the "psycopg" / psycopg3 package — chosen over psycopg2 because it ships
pre-built binary wheels for current Python versions, so it installs with a
plain `pip install` and never needs a local C compiler or PostgreSQL's
build tools). The shim gives conn.execute(sql, params), dict-like rows,
.cursor(), .executescript(), and a .lastrowid on cursors, so that app.py's
existing SQL — written with "?" placeholders and sqlite3.Row-style dict
access — keeps working completely unchanged. The shim also translates the
handful of SQLite-only function calls used in app.py's raw SQL:

  ?                  -> %s               (paramstyle)
  last_insert_rowid() -> lastval()        (id of the last insert on this connection)
  datetime('now')     -> to_char(now(), 'YYYY-MM-DD HH24:MI:SS')
  date('now')         -> to_char(now(), 'YYYY-MM-DD')

Configuration: set the DATABASE_URL environment variable to your Postgres
connection string, e.g.:

    postgres://user:password@host:5432/dbname

Locally, put this in a .env file (never committed — see .gitignore) and
load it with python-dotenv, or export it in your shell before running
python3 app.py. On Render, set DATABASE_URL in the service's Environment
settings, pointing at your Render Postgres instance's internal connection
string.
"""

import os
import re

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv()  # loads DATABASE_URL from a local .env file, if present —
                # done here (not just in app.py) so this module also works
                # when imported directly, e.g. `python3 -c "from db import init_db; init_db()"`

DATABASE_URL = os.environ.get("DATABASE_URL")
SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema_postgres.sql")

_QMARK_RE = re.compile(r"\?")


def _translate(sql):
    sql = sql.replace("last_insert_rowid()", "lastval()")
    sql = sql.replace("datetime('now')", "to_char(now(), 'YYYY-MM-DD HH24:MI:SS')")
    sql = sql.replace("date('now')", "to_char(now(), 'YYYY-MM-DD')")
    sql = _QMARK_RE.sub("%s", sql)
    return sql


class Cursor:
    """Wraps a psycopg (dict_row) cursor to behave like sqlite3's."""

    def __init__(self, pg_cursor):
        self._cur = pg_cursor

    def execute(self, sql, params=()):
        self._cur.execute(_translate(sql), params)
        return self

    def executescript(self, script):
        self._cur.execute(script)
        return self

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def rowcount(self):
        return self._cur.rowcount

    @property
    def lastrowid(self):
        # Emulates sqlite3's cursor.lastrowid: the id from the most
        # recently used sequence on this connection (i.e. the row just
        # inserted into a table with a SERIAL primary key).
        try:
            self._cur.execute("SELECT lastval() AS id")
            row = self._cur.fetchone()
            return row["id"] if row else None
        except Exception:
            return None


class Connection:
    """Wraps a psycopg connection to behave like sqlite3's."""

    def __init__(self, pg_conn):
        self._conn = pg_conn

    def _new_cursor(self):
        return self._conn.cursor(row_factory=dict_row)

    def execute(self, sql, params=()):
        cur = Cursor(self._new_cursor())
        return cur.execute(sql, params)

    def executescript(self, script):
        cur = self._new_cursor()
        cur.execute(script)
        return cur

    def cursor(self):
        return Cursor(self._new_cursor())

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()


def get_db():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and fill in "
            "your Postgres connection string (see README for where to get "
            "it from Render)."
        )
    pg_conn = psycopg.connect(DATABASE_URL)
    return Connection(pg_conn)


def init_db():
    conn = get_db()
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
