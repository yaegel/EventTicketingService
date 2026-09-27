import sqlite3
import os
from contextlib import contextmanager
from config import Config

def get_db_connection():
    """Create a new SQLite connection configured for concurrent access."""
    # Ensure directory exists
    db_dir = os.path.dirname(Config.DATABASE_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    conn = sqlite3.connect(
        Config.DATABASE_PATH,
        timeout=10.0,
        detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES
    )
    conn.row_factory = sqlite3.Row
    # Enable WAL mode for thread concurrency
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn

@contextmanager
def db_session():
    """Context manager for database transactions."""
    conn = get_db_connection()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init_db():
    """Initialize database tables, columns, indexes, and views from scratch."""
    with db_session() as conn:
        # 1. Orders table (TEXT PRIMARY KEY for UUIDv7)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                user_id TEXT,
                seats TEXT,
                fname TEXT DEFAULT NULL,
                lname TEXT DEFAULT NULL,
                email TEXT DEFAULT NULL,
                phone TEXT DEFAULT NULL,
                addr_street TEXT DEFAULT NULL,
                addr_street2 TEXT DEFAULT NULL,
                addr_city TEXT DEFAULT NULL,
                addr_state TEXT DEFAULT NULL,
                addr_zip TEXT DEFAULT NULL,
                cc_name TEXT DEFAULT NULL,
                cc_number TEXT DEFAULT NULL,
                cc_last4 TEXT DEFAULT NULL,
                cc_expiry TEXT DEFAULT NULL,
                payment_transaction_id TEXT DEFAULT NULL,
                held_at TIMESTAMP DEFAULT NULL,
                hold_expires_at TIMESTAMP DEFAULT NULL,
                expired_at TIMESTAMP DEFAULT NULL,
                processing_started_at TIMESTAMP DEFAULT NULL,
                locked_by TEXT DEFAULT NULL,
                processed_at TIMESTAMP DEFAULT NULL,
                delivered_at TIMESTAMP DEFAULT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # 2. Order status history table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS order_status (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (order_id) REFERENCES orders (id) ON DELETE CASCADE
            );
        """)

        # 3. Tickets table
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                seat TEXT NOT NULL,
                ticket_code TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'issued',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (order_id) REFERENCES orders (id) ON DELETE CASCADE
            );
        """)

        # 4. Indexes
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tickets_order_id 
            ON tickets (order_id);
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_processed_created 
            ON orders (processed_at, created_at);
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_claim 
            ON orders (processed_at, processing_started_at, created_at);
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_hold_expires 
            ON orders (hold_expires_at);
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_orders_user_id 
            ON orders (user_id);
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_order_status_order_id 
            ON order_status (order_id, id);
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_order_status_status 
            ON order_status (status);
        """)

        # 4. View for latest order status
        conn.execute("DROP VIEW IF EXISTS v_orders_latest_status;")
        conn.execute("""
            CREATE VIEW v_orders_latest_status AS
            SELECT 
                o.*,
                os.id AS status_id,
                os.status AS status,
                os.created_at AS status_created_at
            FROM orders o
            LEFT JOIN order_status os ON os.id = (
                SELECT id FROM order_status 
                WHERE order_id = o.id 
                ORDER BY id DESC 
                LIMIT 1
            );
        """)
