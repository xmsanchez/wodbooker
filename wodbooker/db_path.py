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
    return f'sqlite:///{path}?check_same_thread=False'
