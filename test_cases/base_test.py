"""
Base test fixture for Event Ticketing Service state machine path tests.
Provides:
- Isolated temporary SQLite database per test.
- Flask test client.
- Payment gateway reset and helper utilities.
"""
import os
import json
import logging
import shutil
import tempfile
import unittest
from typing import Any
from config import Config
from database import init_db
from app import create_app
from payment_gateway import reset_payment_gateway
from worker import stop_worker

logger = logging.getLogger("test")
if not logger.handlers:
    # Ensure logs display cleanly in unittest test runs
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] [%(name)s] %(message)s"
    )

class BaseTestCase(unittest.TestCase):
    def setUp(self):
        # Stop background worker loop to avoid asynchronous race conditions during assertions
        stop_worker()

        # Create isolated temporary directory and SQLite database
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_ticketing.db")
        self.original_db_path = Config.DATABASE_PATH
        Config.DATABASE_PATH = self.db_path

        # Initialize fresh schema in isolated database
        init_db()

        # Reset payment gateway to default AUTO mode
        reset_payment_gateway()

        # Create Flask test client
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()

    def tearDown(self):
        # Restore original database path and clean up temp files
        Config.DATABASE_PATH = self.original_db_path
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir, ignore_errors=True)

    def log_test_description(self, title: str, state_path: str, description: str):
        """Self-describe the test case as a formatted log message."""
        logger.info("\n" + "=" * 80)
        logger.info(f"TEST RUN: {title}")
        logger.info(f"STATE PATH: {state_path}")
        logger.info(f"DESCRIPTION: {description.strip()}")
        logger.info("-" * 80)

    def log_order_details(self, order_id: Any, label: str = "Final Order State"):
        """Fetch and log full order details including complete status history."""
        order = self.get_order(order_id)
        history = [f"{entry['status']} (@ {entry['created_at']})" for entry in order.get("status_history", [])]
        history_chain = " -> ".join([entry["status"] for entry in order.get("status_history", [])])

        formatted_json = json.dumps(order, indent=2)
        logger.info(f"\n[ORDER #{order_id} DETAILS - {label}]")
        logger.info(f"State History Flow: {history_chain}")
        logger.info(f"Chronological History: {history}")
        logger.info(f"Complete Order Record:\n{formatted_json}")
        logger.info("=" * 80 + "\n")

    def get_order(self, order_id: Any):
        """Fetch order details and status history via API."""
        resp = self.client.get(f"/orders/{order_id}")
        self.assertEqual(resp.status_code, 200)
        return resp.get_json()["order"]

    def assert_status_history(self, order_id: Any, expected_states: list):
        """Verify the exact sequence of states recorded in the order_status table."""
        order = self.get_order(order_id)
        actual_states = [entry["status"] for entry in order.get("status_history", [])]
        self.assertEqual(
            actual_states,
            expected_states,
            f"State history mismatch for Order #{order_id}. Expected {expected_states}, got {actual_states}"
        )

    def create_completed_order(self, user_id="test_user", seats=None, cc_number="4111111111114242", fname="Test", lname="User", email="test@example.com"):
        """Helper to create an order from claim through complete."""
        if seats is None:
            seats = ["A1", "A2"]
        claim = self.client.post("/claim_seats", json={"user_id": user_id, "seats": seats})
        order_id = claim.get_json()["order"]["id"]
        self.client.patch(f"/orders/{order_id}", json={
            "contact_info": {
                "fname": fname,
                "lname": lname,
                "email": email,
                "phone": "555-0100",
                "addr_street": "123 Main St",
                "addr_city": "Austin",
                "addr_state": "TX",
                "addr_zip": "78701"
            },
            "payment_info": {
                "cc_name": f"{fname} {lname}",
                "cc_number": cc_number,
                "cc_expiry": "12/28"
            }
        })
        self.client.post(f"/orders/{order_id}/process_payment")
        self.client.post(f"/orders/{order_id}/deliver_tickets")
        return order_id

