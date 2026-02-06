#!/usr/bin/env python
"""
Rollback migration script
Usage: python rollback.py <version> [rollback_file.sql]
       python rollback.py v1.11.0 rollback_class_training_description.sql
"""
import os
import argparse
import logging
from sqlalchemy import create_engine
from sqlalchemy.sql import text
from sqlalchemy.exc import SQLAlchemyError, OperationalError

logging.basicConfig(format='%(asctime)s - %(threadName)s - %(message)s', level=logging.INFO)

def get_rollback_script(version, rollback_file=None):
    """
    Get the rollback script for the given version
    :param version: Version to rollback
    :param rollback_file: Specific rollback file (optional)
    """
    migration_dir = f'migrations/{version}'
    
    if not os.path.exists(migration_dir):
        logging.error(f"Migration directory {migration_dir} not found")
        return None
    
    # If specific file requested, use it
    if rollback_file:
        file_path = os.path.join(migration_dir, rollback_file)
        if os.path.exists(file_path):
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        else:
            logging.error(f"Rollback file {file_path} not found")
            return None
    
    # Otherwise, look for rollback_*.sql files
    rollback_files = [f for f in os.listdir(migration_dir) 
                     if f.endswith('.sql') and f.startswith('rollback_')]
    
    if not rollback_files:
        logging.error(f"No rollback scripts found in {migration_dir}")
        logging.info("Create a rollback script named 'rollback_<migration_name>.sql'")
        return None
    
    if len(rollback_files) > 1:
        logging.warning(f"Multiple rollback scripts found: {rollback_files}")
        logging.warning("Using the first one. Specify a file explicitly if needed.")
    
    file_path = os.path.join(migration_dir, rollback_files[0])
    with open(file_path, 'r', encoding='utf-8') as f:
        return f.read()


def execute_rollback(script):
    """
    Execute the rollback script
    :param script: The rollback SQL script
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
        return False
    
    engine = create_engine(f'sqlite:///{db_path}')
    conn = engine.connect()
    
    logging.warning("=" * 60)
    logging.warning("WARNING: You are about to rollback database changes!")
    logging.warning("This may result in DATA LOSS!")
    logging.warning("=" * 60)
    
    script_statements = [s.strip() for s in script.split(";") if s.strip()]
    
    for statement in script_statements:
        if not statement:
            continue
        try:
            logging.info("Executing: %s", statement[:100] + "..." if len(statement) > 100 else statement)
            conn.execute(text(statement))
        except OperationalError as e:
            error_msg = str(e).lower()
            if 'no such table' in error_msg or 'no such index' in error_msg:
                logging.warning("Table or index doesn't exist (may already be rolled back): %s", statement[:50])
                continue
            logging.error("Error executing rollback: %s", e)
            logging.error("Statement was: %s", statement[:200])
            conn.close()
            return False
        except SQLAlchemyError as e:
            logging.error("Error executing rollback: %s", e)
            logging.error("Statement was: %s", statement[:200])
            conn.close()
            return False
    
    conn.commit()
    conn.close()
    logging.info("Rollback completed successfully")
    return True


if __name__ == '__main__':
    argparser = argparse.ArgumentParser(
        description='Rollback a database migration',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python rollback.py v1.11.0
  python rollback.py v1.11.0 rollback_class_training_description.sql
        """
    )
    argparser.add_argument('version', type=str, help='Version to rollback (e.g., v1.11.0)')
    argparser.add_argument('rollback_file', type=str, nargs='?', 
                          help='Specific rollback file (optional)')
    
    args = argparser.parse_args()
    
    script = get_rollback_script(args.version, args.rollback_file)
    if script:
        success = execute_rollback(script)
        exit(0 if success else 1)
    else:
        exit(1)

