import os

class Config:
    DATABASE_PATH = os.environ.get("DATABASE_PATH", "data/ticketing.db")
    WORKER_POLL_INTERVAL_SECONDS = float(os.environ.get("WORKER_POLL_INTERVAL_SECONDS", "3.0"))
    HOST = os.environ.get("HOST", "0.0.0.0")
    PORT = int(os.environ.get("PORT", "5000"))
    DEBUG = os.environ.get("DEBUG", "true").lower() in ("true", "1", "yes")

    # Seat hold TTL in minutes before expiring
    SEAT_HOLD_TTL = float(os.environ.get("SEAT_HOLD_TTL", "10.0"))

    # Retention period in minutes before cleaning up expired records from database
    EXPIRED_RETENTION_TTL = float(os.environ.get("EXPIRED_RETENTION_TTL", "5.0"))

    # Default ticket price in cents per seat ($50.00)
    TICKET_PRICE_CENTS = int(os.environ.get("TICKET_PRICE_CENTS", "5000"))

    # Simulated external work delay in seconds for API endpoints (gateway, ticket gen, delivery)
    SIMULATE_WORK_DELAY_SECONDS = float(os.environ.get("SIMULATE_WORK_DELAY_SECONDS", "0.05"))

    # Number of concurrent background worker threads
    WORKER_THREAD_COUNT = int(os.environ.get("WORKER_THREAD_COUNT", "2"))

    # Worker lock timeout in minutes (stale lock threshold)
    WORKER_LOCK_TIMEOUT_MINUTES = float(os.environ.get("WORKER_LOCK_TIMEOUT_MINUTES", "5.0"))
