import json
import threading
import logging
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from config import Config
from database import db_session
from state_machine import OrderState, transition_order

logger = logging.getLogger("worker")

class BackgroundWorker:
    def __init__(self, poll_interval: float = None, app=None, base_url: str = None):
        self.poll_interval = poll_interval or Config.WORKER_POLL_INTERVAL_SECONDS
        self._stop_event = threading.Event()
        self._threads: list[threading.Thread] = []
        self.app = app
        self.base_url = base_url
        self._client = None

    @property
    def _thread(self):
        """Backward compatibility property returning the primary worker thread."""
        return self._threads[0] if self._threads else None

    def set_app(self, app):
        """Configure or update the Flask application instance for API calls."""
        self.app = app
        self._client = app.test_client() if app else None

    def _get_client(self):
        """Retrieve or lazily initialize Flask test_client for internal dispatch."""
        if self._client is not None:
            return self._client
        if self.app is not None:
            self._client = self.app.test_client()
            return self._client
        try:
            import app as flask_app_module
            flask_app = getattr(flask_app_module, "app", None)
            if flask_app is not None:
                self.app = flask_app
                self._client = flask_app.test_client()
                return self._client
        except Exception:
            pass
        return None

    def call_api(self, method: str, endpoint: str, json_data: dict = None) -> tuple[int, dict]:
        """
        Invoke application API endpoints for payment processing, ticket generation, and delivery.
        Prefers in-memory Flask test_client (thread-safe, zero network latency, socket-independent).
        Falls back to HTTP via urllib.request if running against an external server.
        """
        # 1. Try Flask test_client
        client = self._get_client()
        if client:
            if method.upper() == "POST":
                resp = client.post(endpoint, json=json_data or {})
            else:
                resp = client.get(endpoint)
            data = resp.get_json(silent=True) or {}
            return resp.status_code, data

        # 2. HTTP fallback via urllib
        base = (self.base_url or f"http://127.0.0.1:{Config.PORT}").rstrip("/")
        url = f"{base}{endpoint}"
        req_data = json.dumps(json_data or {}).encode("utf-8") if json_data is not None else None
        headers = {"Content-Type": "application/json"} if req_data else {}
        req = urllib.request.Request(url, data=req_data, headers=headers, method=method.upper())
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                body = response.read().decode("utf-8")
                return response.status, json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            return e.code, json.loads(body) if body else {}
        except Exception as e:
            logger.error(f"[Worker] API call to {url} failed: {e}")
            return 500, {"error": str(e)}

    def start(self, thread_count: int = None):
        """Start the background worker threads."""
        if any(t.is_alive() for t in self._threads):
            logger.warning("Worker threads are already running.")
            return

        self._stop_event.clear()
        self._threads = []
        num_threads = thread_count or getattr(Config, "WORKER_THREAD_COUNT", 2)
        for i in range(num_threads):
            t = threading.Thread(
                target=self._run_loop,
                daemon=True,
                name=f"OrderProcessorWorker-{i+1}"
            )
            t.start()
            self._threads.append(t)
        logger.info(f"Started {len(self._threads)} background worker thread(s). Polling interval: {self.poll_interval}s")

    def stop(self):
        """Signal all worker threads to stop and wait for them to join."""
        if not self._threads:
            return
        logger.info("Stopping background worker...")
        self._stop_event.set()
        for t in self._threads:
            t.join(timeout=5.0)
        self._threads = []
        logger.info("Background worker stopped.")

    def _run_loop(self):
        """
        Main loop:
        1. Expire stale seat holds past Config.SEAT_HOLD_TTL
        2. Clean up (delete) expired records older than retention TTL
        3. Process oldest unprocessed active record via API endpoints
        """
        while not self._stop_event.is_set():
            try:
                expired_count = self.expire_stale_holds()
                cleaned_count = self.cleanup_expired_orders()
                processed = self.process_oldest_record()

                if not (expired_count or cleaned_count or processed):
                    # No work was performed; wait for next poll interval or stop signal
                    self._stop_event.wait(timeout=self.poll_interval)
            except Exception as e:
                logger.error(f"Error in background worker loop: {e}", exc_info=True)
                self._stop_event.wait(timeout=self.poll_interval)

    def expire_stale_holds(self) -> int:
        """
        Find orders in 'held' status where hold_expires_at has passed.
        Transition their status to 'expired'.
        Returns number of holds expired.
        """
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        with db_session() as conn:
            rows = conn.execute("""
                SELECT id, status, held_at, hold_expires_at
                FROM v_orders_latest_status
                WHERE status = 'held'
                  AND hold_expires_at IS NOT NULL
                  AND hold_expires_at <= ?
            """, (now_str,)).fetchall()

            if not rows:
                return 0

            for row in rows:
                order_id = row["id"]
                logger.info(
                    f"[Worker] Expiring stale seat hold for Order #{order_id} "
                    f"(status='{row['status']}', held_at='{row['held_at']}', expires_at='{row['hold_expires_at']}')"
                )
                transition_order(conn, order_id, OrderState.EXPIRED)

            return len(rows)

    def cleanup_expired_orders(self) -> int:
        """
        Find orders in 'expired' status where expired_at (or updated_at) is older than
        Config.EXPIRED_RETENTION_TTL minutes.
        Permanently delete them from the database.
        Returns number of deleted orders.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=Config.EXPIRED_RETENTION_TTL)
        cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")

        with db_session() as conn:
            rows = conn.execute("""
                SELECT id, expired_at, updated_at
                FROM v_orders_latest_status
                WHERE status = ?
                  AND (
                    (expired_at IS NOT NULL AND expired_at <= ?)
                    OR (expired_at IS NULL AND updated_at <= ?)
                  )
            """, (OrderState.EXPIRED.value, cutoff_str, cutoff_str)).fetchall()

            if not rows:
                return 0

            for row in rows:
                order_id = row["id"]
                logger.info(
                    f"[Worker] Cleaning up / deleting expired Order #{order_id} from database "
                    f"(expired_at='{row['expired_at'] or row['updated_at']}', cutoff='{cutoff_str}')"
                )
                conn.execute("DELETE FROM order_status WHERE order_id = ?", (order_id,))
                conn.execute("DELETE FROM orders WHERE id = ?", (order_id,))

            return len(rows)

    def process_oldest_record(self, worker_name: str = None) -> bool:
        """
        Atomically query and claim the oldest database record where processed_at is NULL,
        locking it with a timestamp and worker identifier to prevent duplicate processing
        across concurrent worker threads.
        
        Explicitly executes order fulfillment pipeline by invoking API endpoints:
        1. Payment processing: POST /orders/<id>/process_payment (for 'initialized')
        2. Ticket fulfillment: POST /orders/<id>/deliver_tickets (generates tickets if missing & dispatches)
        
        Returns True if an order was claimed and processed, False otherwise.
        """
        worker_id = worker_name or threading.current_thread().name
        now_utc = datetime.now(timezone.utc)
        now_str = now_utc.strftime("%Y-%m-%d %H:%M:%S")
        lock_timeout_mins = getattr(Config, "WORKER_LOCK_TIMEOUT_MINUTES", 5.0)
        timeout_cutoff = (now_utc - timedelta(minutes=lock_timeout_mins)).strftime("%Y-%m-%d %H:%M:%S")

        with db_session() as conn:
            # Atomic claim: Lock the oldest eligible record in orders
            cursor = conn.execute("""
                UPDATE orders
                SET processing_started_at = ?,
                    locked_by = ?
                WHERE id = (
                    SELECT id FROM v_orders_latest_status
                    WHERE processed_at IS NULL
                      AND (
                        processing_started_at IS NULL
                        OR processing_started_at <= ?
                      )
                      AND status IN ('initialized', 'payment_authorized', 'complete')
                    ORDER BY created_at ASC
                    LIMIT 1
                )
            """, (now_str, worker_id, timeout_cutoff))

            if cursor.rowcount == 0:
                return False

            # Fetch the row claimed by this worker
            row = conn.execute("""
                SELECT id, user_id, seats, status, created_at,
                       cc_number, email
                FROM v_orders_latest_status
                WHERE locked_by = ?
                  AND processing_started_at = ?
                  AND processed_at IS NULL
                ORDER BY created_at ASC
                LIMIT 1
            """, (worker_id, now_str)).fetchone()

            if not row:
                return False

            order_id = row["id"]
            current_status = row["status"]
            created_at = row["created_at"]

        logger.info(
            f"[{worker_id}] Claimed and processing oldest record: Order #{order_id} "
            f"(status='{current_status}', created_at='{created_at}')"
        )

        completed_work = False
        try:
            # Step 1: Payment processing (for initialized orders)
            if current_status == OrderState.INITIALIZED.value:
                logger.info(f"[{worker_id}] Calling API endpoint: POST /orders/{order_id}/process_payment")
                status_code, pay_resp = self.call_api("POST", f"/orders/{order_id}/process_payment")
                if status_code == 200:
                    logger.info(
                        f"[{worker_id}] Payment processing for Order #{order_id} succeeded (HTTP {status_code}). "
                        "Order transitioned to 'payment_authorized' in database."
                    )
                else:
                    logger.warning(
                        f"[{worker_id}] Payment processing for Order #{order_id} failed (HTTP {status_code}): "
                        f"{pay_resp.get('error', 'Payment declined')}. Order transitioned to 'payment_failed' in database."
                    )
                # Release lock so subsequent cycles can process the next state
                with db_session() as conn:
                    conn.execute("""
                        UPDATE orders
                        SET processing_started_at = NULL,
                            locked_by = NULL
                        WHERE id = ? AND locked_by = ?
                    """, (order_id, worker_id))
                completed_work = True
                return status_code in (200, 402)

            # Step 2: Ticket fulfillment (creates tickets if missing & dispatches)
            if current_status in (OrderState.PAYMENT_AUTHORIZED.value, OrderState.COMPLETE.value):
                logger.info(f"[{worker_id}] Calling API endpoint: POST /orders/{order_id}/deliver_tickets")
                del_code, del_resp = self.call_api("POST", f"/orders/{order_id}/deliver_tickets")
                if del_code != 200:
                    logger.error(
                        f"[{worker_id}] Delivery for Order #{order_id} failed "
                        f"(HTTP {del_code}): {del_resp.get('error')}"
                    )
                    with db_session() as conn:
                        conn.execute("""
                            UPDATE orders
                            SET processing_started_at = NULL,
                                locked_by = NULL
                            WHERE id = ? AND locked_by = ?
                        """, (order_id, worker_id))
                    completed_work = True
                    return True

                # Step 3: Mark order as processed in database and release lock
                now_finish = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                with db_session() as conn:
                    conn.execute("""
                        UPDATE orders
                        SET processed_at = ?,
                            processing_started_at = NULL,
                            locked_by = NULL,
                            updated_at = ?
                        WHERE id = ?
                    """, (now_finish, now_finish, order_id))

                logger.info(
                    f"[{worker_id}] Finished fulfillment for Order #{order_id}: "
                    f"tickets generated, delivery completed, and marked as processed."
                )
                completed_work = True
                return True

            return False

        finally:
            if not completed_work:
                # Release lock if an unexpected exception aborted processing
                with db_session() as conn:
                    conn.execute("""
                        UPDATE orders
                        SET processing_started_at = NULL,
                            locked_by = NULL
                        WHERE id = ? AND locked_by = ? AND processed_at IS NULL
                    """, (order_id, worker_id))

# Singleton worker instance
worker_instance = BackgroundWorker()

def start_worker(app=None, thread_count: int = None):
    if app is not None:
        worker_instance.set_app(app)
    worker_instance.start(thread_count=thread_count)

def stop_worker():
    worker_instance.stop()
