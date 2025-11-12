import os
import argparse
import logging
from sqlalchemy import create_engine
from sqlalchemy.sql import text
from sqlalchemy.exc import SQLAlchemyError, OperationalError

logging.basicConfig(format='%(asctime)s - %(threadName)s - %(message)s', level=logging.INFO)

def get_migrate_scripts(version):
    """
    Get the migration scripts for the given version
    :param version: Version to migrate to
    """
    _migrations = {}

    if not os.path.exists(f'migrations/{version}'):
        return _migrations

    for file in os.listdir(f'migrations/{version}'):
        if file.endswith('.sql'):
            with open(f'migrations/{version}/{file}', 'r', encoding='utf-8') as f:
                _migrations[file] = f.read()

    return _migrations


def execute_migration(_migrations):
    """
    Execute the given migration scripts
    :param migrations: Dictionary with the migration scripts
    """
    import os
    # Check if database is in instance/ or in the current directory
    db_path = 'instance/db.sqlite'
    if not os.path.exists(db_path):
        db_path = 'db.sqlite'
    if not os.path.exists(db_path):
        # Try in wodbooker directory
        db_path = 'wodbooker/db.sqlite'
    
    if not os.path.exists(db_path):
        logging.error("Database file not found. Tried: instance/db.sqlite, db.sqlite, wodbooker/db.sqlite")
        return
    
    engine = create_engine(f'sqlite:///{db_path}')
    conn = engine.connect()

    for name, script in _migrations.items():
        logging.info("Executing migration script %s", name)
        script_statements = filter(lambda x: x, script.split(";"))
        for statement in script_statements:
            statement = statement.strip()
            if not statement:
                continue
            try:
                conn.execute(text(statement))
            except OperationalError as e:
                # Check if it's a "duplicate column" error, which is OK
                error_msg = str(e).lower()
                if 'duplicate column' in error_msg or 'already exists' in error_msg:
                    logging.warning("Column or table already exists, skipping: %s", statement[:50])
                    continue
                logging.error("Error executing migration %s: %s", name, e)
                logging.error("Statement was: %s", statement[:200])
                conn.close()
                return
            except SQLAlchemyError as e:
                logging.error("Error executing migration %s: %s", name, e)
                logging.error("Statement was: %s", statement[:200])
                conn.close()
                return

        logging.info("Migration %s executed successfully", name)

    conn.commit()
    conn.close()


if __name__ == '__main__':
    argparser = argparse.ArgumentParser()
    argparser.add_argument('version', type=str, help='Version to migrate to')

    args = argparser.parse_args()

    migrations = get_migrate_scripts(args.version)
    execute_migration(migrations)
