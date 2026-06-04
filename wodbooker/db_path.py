"""SQLite database path resolution (no Flask imports — safe for migrate.py)."""
import os
import os.path as op

_PKG_DIR = op.realpath(op.dirname(__file__))
PROJECT_ROOT = op.dirname(_PKG_DIR)


def resolve_database_path() -> str:
    """
    Canonical database: <project_root>/instance/db.sqlite (Flask instance folder).
    Creates instance/ if missing.
    """
    instance_dir = op.join(PROJECT_ROOT, 'instance')
    os.makedirs(instance_dir, exist_ok=True)
    return op.join(instance_dir, 'db.sqlite')


def sqlalchemy_sqlite_uri(database_path: str) -> str:
    path = op.abspath(database_path).replace('\\', '/')
    return f'sqlite:///{path}'


def sqlite_connect_args() -> dict:
    """SQLite driver options (busy_timeout in seconds for lock waits)."""
    return {'check_same_thread': False, 'timeout': 30}


def apply_sqlite_pragmas(dbapi_connection) -> None:
    """Improve concurrent read/write behaviour for multi-threaded Flask + booker."""
    cursor = dbapi_connection.cursor()
    cursor.execute('PRAGMA journal_mode=WAL')
    cursor.execute('PRAGMA busy_timeout=30000')
    cursor.execute('PRAGMA synchronous=NORMAL')
    cursor.close()


def db_commit_with_retry(session, *, max_attempts: int = 5, base_delay: float = 0.05) -> None:
    """Commit with retries when SQLite reports database is locked/busy."""
    import time
    from sqlalchemy.exc import OperationalError

    last_exc = None
    for attempt in range(max_attempts):
        try:
            session.commit()
            return
        except OperationalError as exc:
            last_exc = exc
            session.rollback()
            msg = str(exc).lower()
            if 'locked' not in msg and 'busy' not in msg:
                raise
            if attempt + 1 >= max_attempts:
                raise
            time.sleep(base_delay * (2 ** attempt))
    if last_exc is not None:
        raise last_exc
