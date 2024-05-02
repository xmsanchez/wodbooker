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
    engine = create_engine('sqlite:///instance/db.sqlite')
    conn = engine.connect()

    for name, script in _migrations.items():
        logging.info("Executing migration script %s", name)
        script_statements = filter(lambda x: x, script.split(";"))
        for statement in script_statements:
            try:
                conn.execute(text(statement))
            except OperationalError as e:
                logging.error("Error executing migration %s: %s", name, e)
                conn.close()
                return
            except SQLAlchemyError as e:
                logging.error("Error executing migration %s: %s", name, e)
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
