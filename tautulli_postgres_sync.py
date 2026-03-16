#!/usr/bin/env python3
"""
Tautulli to PostgreSQL Sync Script
===================================
Synchronizes Tautulli SQLite database to PostgreSQL for Grafana analytics

Features:
- Automatic user mapping (handles username changes)
- Full schema sync with indexes
- Incremental sync (only new data)
- Configurable via environment variables or JSON file

Usage in Unraid User Scripts:
- Set to run daily (or as needed)
- Requires PostgreSQL container running
- First run: Full import (may take time with 5 years of data)
- Subsequent runs: Only sync new data

Author: Community project for Plex monitoring
"""

import sqlite3
import psycopg2
from psycopg2 import sql, extras
import sys
import logging
from datetime import datetime
from pathlib import Path
import os
import json

# ============================================================================
# CONFIGURATION - USES ENVIRONMENT VARIABLES FOR DOCKER
# ============================================================================

# Tautulli Database
TAUTULLI_DB = os.getenv('TAUTULLI_DB', '/data/tautulli.db')

# PostgreSQL Connection
POSTGRES_CONFIG = {
    'host': os.getenv('POSTGRES_HOST', 'localhost'),
    'port': int(os.getenv('POSTGRES_PORT', '5432')),
    'database': os.getenv('POSTGRES_DB', 'tautulli'),
    'user': os.getenv('POSTGRES_USER', 'tautulli'),
    'password': os.getenv('POSTGRES_PASSWORD', 'change_me')
}

# Logging
LOG_FILE = os.getenv('LOG_FILE', '/logs/sync.log')
LOG_LEVEL = logging.INFO

# User Mapping Configuration
USER_MAPPING_FILE = os.getenv('USER_MAPPING_FILE', '/config/user_mapping.json')
USER_MAPPING_ENV = os.getenv('USER_MAPPING', '')  # Format: old:new,old:new,...

# Global user mapping dictionary
USER_MAPPING = {}

# ============================================================================
# TABLES TO SYNC (in order due to foreign key dependencies)
# ============================================================================

TABLES_TO_SYNC = [
    'users',
    'library_sections',
    'session_history',
    'session_history_metadata',
    'session_history_media_info',
]

# ============================================================================
# USER MAPPING FUNCTIONS
# ============================================================================

def load_user_mapping():
    """Load user mapping from JSON file or environment variable"""
    global USER_MAPPING
    
    # Try loading from JSON file first
    if os.path.exists(USER_MAPPING_FILE):
        try:
            with open(USER_MAPPING_FILE, 'r') as f:
                config = json.load(f)
                USER_MAPPING = config.get('user_mapping', {})
                logging.info(f"Loaded user mapping from {USER_MAPPING_FILE}: {len(USER_MAPPING)} mappings")
                return
        except Exception as e:
            logging.warning(f"Failed to load user mapping from file: {e}")
    
    # Fall back to environment variable
    if USER_MAPPING_ENV:
        try:
            # Parse format: old:new,old:new,...
            for pair in USER_MAPPING_ENV.split(','):
                if ':' in pair:
                    old, new = pair.split(':', 1)
                    USER_MAPPING[old.strip()] = new.strip()
            logging.info(f"Loaded user mapping from environment: {len(USER_MAPPING)} mappings")
        except Exception as e:
            logging.warning(f"Failed to parse USER_MAPPING environment variable: {e}")
    
    if not USER_MAPPING:
        logging.info("No user mapping configured (this is OK if not needed)")

def normalize_username(username):
    """Normalize username using mapping"""
    return USER_MAPPING.get(username, username)

# ============================================================================
# SETUP LOGGING
# ============================================================================

def setup_logging():
    """Setup logging to file and console"""
    log_dir = Path(LOG_FILE).parent
    log_dir.mkdir(parents=True, exist_ok=True)
    
    logging.basicConfig(
        level=LOG_LEVEL,
        format='%(asctime)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE),
            logging.StreamHandler(sys.stdout)
        ]
    )

# ============================================================================
# DATABASE CONNECTIONS
# ============================================================================

def get_sqlite_connection():
    """Connect to Tautulli SQLite database"""
    try:
        conn = sqlite3.connect(TAUTULLI_DB)
        conn.row_factory = sqlite3.Row
        logging.info(f"Connected to SQLite: {TAUTULLI_DB}")
        return conn
    except sqlite3.Error as e:
        logging.error(f"Failed to connect to SQLite: {e}")
        sys.exit(1)

def get_postgres_connection():
    """Connect to PostgreSQL database"""
    try:
        conn = psycopg2.connect(**POSTGRES_CONFIG)
        conn.autocommit = False
        logging.info(f"Connected to PostgreSQL: {POSTGRES_CONFIG['host']}:{POSTGRES_CONFIG['port']}")
        return conn
    except psycopg2.Error as e:
        logging.error(f"Failed to connect to PostgreSQL: {e}")
        sys.exit(1)

# ============================================================================
# SCHEMA CREATION
# ============================================================================

def get_sqlite_table_schema(sqlite_conn, table_name):
    """Get table schema from SQLite"""
    cursor = sqlite_conn.cursor()
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = cursor.fetchall()
    return columns

def sqlite_type_to_postgres(sqlite_type):
    """Convert SQLite type to PostgreSQL type"""
    sqlite_type = sqlite_type.upper()
    
    if 'INT' in sqlite_type:
        return 'INTEGER'
    elif 'TEXT' in sqlite_type or 'CHAR' in sqlite_type or 'CLOB' in sqlite_type:
        return 'TEXT'
    elif 'BLOB' in sqlite_type:
        return 'TEXT'
    elif 'REAL' in sqlite_type or 'FLOA' in sqlite_type or 'DOUB' in sqlite_type:
        return 'REAL'
    else:
        return 'TEXT'  # Default to TEXT

def create_postgres_schema(pg_conn, sqlite_conn):
    """Create PostgreSQL schema dynamically from SQLite structure"""
    cursor = pg_conn.cursor()
    sqlite_cursor = sqlite_conn.cursor()
    
    logging.info("Creating PostgreSQL schema...")
    
    # Create sync metadata table FIRST
    try:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sync_metadata (
                table_name TEXT PRIMARY KEY,
                last_sync_timestamp TIMESTAMP,
                last_sync_id INTEGER,
                total_rows INTEGER
            )
        """)
        pg_conn.commit()
        logging.info("Created sync_metadata table")
    except Exception as e:
        pg_conn.rollback()
        logging.error(f"Failed to create sync_metadata table: {e}")
    
    # Get list of tables to sync from SQLite
    for table_name in TABLES_TO_SYNC:
        logging.info(f"Creating table schema for: {table_name}")
        
        try:
            # Get SQLite schema
            columns = get_sqlite_table_schema(sqlite_conn, table_name)
            
            if not columns:
                logging.warning(f"Table {table_name} not found in SQLite, skipping")
                continue
            
            # Build CREATE TABLE statement
            column_defs = []
            primary_key = None
            
            for col in columns:
                col_id, col_name, col_type, not_null, default_val, is_pk = col
                
                # Handle reserved keywords
                if col_name.lower() in ['user', 'group', 'order', 'table']:
                    col_name_quoted = f'"{col_name}"'
                else:
                    col_name_quoted = col_name
                
                pg_type = sqlite_type_to_postgres(col_type)
                
                col_def = f"{col_name_quoted} {pg_type}"
                
                if is_pk:
                    primary_key = col_name_quoted
                    col_def += " PRIMARY KEY"
                
                column_defs.append(col_def)
            
            # Create table
            create_sql = f"CREATE TABLE IF NOT EXISTS {table_name} ({', '.join(column_defs)})"
            
            cursor.execute(create_sql)
            pg_conn.commit()
            logging.info(f"  Created table {table_name} with {len(column_defs)} columns")
            
        except Exception as e:
            pg_conn.rollback()
            logging.error(f"  Failed to create table {table_name}: {e}")
            continue
    
    # Create indexes for better query performance
    logging.info("Creating indexes...")
    
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_session_history_started ON session_history(started)",
        "CREATE INDEX IF NOT EXISTS idx_session_history_user_id ON session_history(user_id)",
        "CREATE INDEX IF NOT EXISTS idx_session_history_user ON session_history(\"user\")",
        "CREATE INDEX IF NOT EXISTS idx_session_history_rating_key ON session_history(rating_key)",
        "CREATE INDEX IF NOT EXISTS idx_session_history_media_type ON session_history(media_type)",
        "CREATE INDEX IF NOT EXISTS idx_session_history_reference_id ON session_history(reference_id)",
        "CREATE INDEX IF NOT EXISTS idx_session_history_metadata_rating_key ON session_history_metadata(rating_key)",
        "CREATE INDEX IF NOT EXISTS idx_session_history_metadata_title ON session_history_metadata(title)",
        "CREATE INDEX IF NOT EXISTS idx_session_history_metadata_grandparent_title ON session_history_metadata(grandparent_title)",
    ]
    
    for index_sql in indexes:
        try:
            cursor.execute(index_sql)
            pg_conn.commit()
        except Exception as e:
            pg_conn.rollback()
            logging.warning(f"Could not create index: {e}")
    
    logging.info("Schema created successfully")

# ============================================================================
# DATA SYNC FUNCTIONS
# ============================================================================

def get_last_sync_id(pg_conn, table_name):
    """Get the last synced ID for a table"""
    cursor = pg_conn.cursor()
    cursor.execute(
        "SELECT last_sync_id FROM sync_metadata WHERE table_name = %s",
        (table_name,)
    )
    result = cursor.fetchone()
    return result[0] if result else 0

def update_sync_metadata(pg_conn, table_name, last_id, total_rows):
    """Update sync metadata after successful sync"""
    cursor = pg_conn.cursor()
    cursor.execute("""
        INSERT INTO sync_metadata (table_name, last_sync_timestamp, last_sync_id, total_rows)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (table_name) 
        DO UPDATE SET 
            last_sync_timestamp = EXCLUDED.last_sync_timestamp,
            last_sync_id = EXCLUDED.last_sync_id,
            total_rows = EXCLUDED.total_rows
    """, (table_name, datetime.now(), last_id, total_rows))
    pg_conn.commit()

def convert_sqlite_row_to_postgres(row, column_names, column_types):
    """Convert SQLite row to PostgreSQL compatible format
    
    Handles:
    - Empty strings in INTEGER columns -> NULL
    - User mapping for 'user' and 'username' columns
    - Type conversions
    """
    converted = []
    for i, value in enumerate(row):
        col_name = column_names[i]
        col_type = column_types[i]
        
        # Apply user mapping if this is the 'user' or 'username' column
        if col_name in ['user', 'username'] and value:
            value = normalize_username(value)
        
        # Handle empty strings in INTEGER columns
        if col_type == 'INTEGER' and value == '':
            converted.append(None)
        # Handle empty strings in REAL columns
        elif col_type == 'REAL' and value == '':
            converted.append(None)
        else:
            converted.append(value)
    
    return tuple(converted)

def sync_table(sqlite_conn, pg_conn, table_name):
    """Sync a single table from SQLite to PostgreSQL"""
    logging.info(f"Syncing table: {table_name}")
    
    sqlite_cursor = sqlite_conn.cursor()
    pg_cursor = pg_conn.cursor()
    
    # Get last synced ID
    last_sync_id = get_last_sync_id(pg_conn, table_name)
    logging.info(f"  Last synced ID: {last_sync_id}")
    
    # Get column types from SQLite schema
    schema_info = get_sqlite_table_schema(sqlite_conn, table_name)
    column_types = [sqlite_type_to_postgres(col[2]) for col in schema_info]
    
    # Get new rows from SQLite
    sqlite_cursor.execute(f"SELECT * FROM {table_name} WHERE id > ? ORDER BY id", (last_sync_id,))
    
    rows = sqlite_cursor.fetchall()
    if not rows:
        logging.info(f"  No new rows to sync")
        return
    
    logging.info(f"  Found {len(rows)} new rows")
    
    # Get column names
    column_names = [description[0] for description in sqlite_cursor.description]
    
    # Quote reserved keywords
    quoted_columns = []
    for col in column_names:
        if col.lower() in ['user', 'group', 'order', 'table']:
            quoted_columns.append(f'"{col}"')
        else:
            quoted_columns.append(col)
    
    # Prepare INSERT statement
    insert_query = sql.SQL("""
        INSERT INTO {} ({}) VALUES ({})
        ON CONFLICT (id) DO UPDATE SET {}
    """).format(
        sql.Identifier(table_name),
        sql.SQL(', ').join(map(sql.SQL, quoted_columns)),
        sql.SQL(', ').join(sql.Placeholder() * len(column_names)),
        sql.SQL(', ').join(
            sql.SQL("{} = EXCLUDED.{}").format(sql.SQL(col), sql.SQL(col))
            for col in quoted_columns if col != 'id' and col != '"id"'
        )
    )
    
    # Insert data in batches
    batch_size = 1000
    total_inserted = 0
    
    for i in range(0, len(rows), batch_size):
        batch = rows[i:i + batch_size]
        
        # Convert rows to PostgreSQL compatible format (includes user mapping)
        converted_batch = [convert_sqlite_row_to_postgres(row, column_names, column_types) for row in batch]
        
        try:
            extras.execute_batch(pg_cursor, insert_query, converted_batch)
            pg_conn.commit()
            total_inserted += len(batch)
            logging.info(f"  Inserted batch: {total_inserted}/{len(rows)}")
        except psycopg2.Error as e:
            pg_conn.rollback()
            logging.error(f"  Error inserting batch: {e}")
            # Try inserting one by one to find problematic row
            for row in converted_batch:
                try:
                    pg_cursor.execute(insert_query, row)
                    pg_conn.commit()
                    total_inserted += 1
                except psycopg2.Error as e2:
                    pg_conn.rollback()
                    logging.error(f"  Failed to insert row ID {row[0]}: {e2}")
    
    # Update sync metadata
    max_id = max(row[0] for row in rows)
    update_sync_metadata(pg_conn, table_name, max_id, total_inserted)
    
    logging.info(f"  Sync complete: {total_inserted} rows inserted")

# ============================================================================
# MAIN SYNC PROCESS
# ============================================================================

def main():
    """Main sync process"""
    setup_logging()
    
    logging.info("=" * 70)
    logging.info("Tautulli to PostgreSQL Sync - Starting")
    logging.info("=" * 70)
    
    # Load user mapping
    load_user_mapping()
    
    # Connect to databases
    sqlite_conn = get_sqlite_connection()
    pg_conn = get_postgres_connection()
    
    try:
        # Create schema if needed
        create_postgres_schema(pg_conn, sqlite_conn)
        
        # Sync each table
        for table_name in TABLES_TO_SYNC:
            try:
                sync_table(sqlite_conn, pg_conn, table_name)
            except Exception as e:
                logging.error(f"Failed to sync table {table_name}: {e}")
                continue
        
        logging.info("=" * 70)
        logging.info("Sync completed successfully")
        logging.info("=" * 70)
        
    except Exception as e:
        logging.error(f"Sync failed: {e}")
        sys.exit(1)
    finally:
        sqlite_conn.close()
        pg_conn.close()

if __name__ == '__main__':
    main()