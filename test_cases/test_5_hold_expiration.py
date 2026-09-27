"""
Test Case 5: Seat Hold Expiration & Cart Abandonment
State Progression: held -> expired

Walkthrough:
1. Customer reserves seat ('held') with a temporary hold TTL.
2. Time elapses past Config.seat_hold_ttl (simulated in database).
3. An attempt to advance checkout on an expired hold is rejected by state machine guard
   (_guard_held_not_expired), proactively transitioning the order to 'expired' (HTTP 400).
4. Background worker's expire_stale_holds() independently transitions any abandoned holds
   to 'expired'.
5. Once expired, the reserved seats are freed and can be immediately claimed by other customers.
6. Verifies audit trail: ['held', 'expired'].
"""
import unittest
from datetime import datetime, timezone, timedelta
from database import db_session
from worker import worker_instance
from test_cases.base_test import BaseTestCase

class TestHoldExpiration(BaseTestCase):
    def test_hold_expiration_and_seat_recovery(self):
        self.log_test_description(
            title="Test Case 5: Seat Hold Expiration & Cart Abandonment",
            state_path="held -> expired",
            description="Exercises seat hold TTL expiration: simulate hold expiry in database. First order is rejected and expired by state machine domain guard (_guard_held_not_expired) on checkout. Second order is swept and expired by background worker. Released seats are verified as reclaimable."
        )

        # ------------------------------------------------------------------
        # Step 1: Customer reserves seat E1 -> 'held'
        # ------------------------------------------------------------------
        claim_res = self.client.post("/claim_seats", json={
            "user_id": "evan_abandon",
            "seats": ["E1"]
        })
        self.assertEqual(claim_res.status_code, 201)
        order_id = claim_res.get_json()["order"]["id"]
        self.assertEqual(claim_res.get_json()["order"]["status"], "held")

        # ------------------------------------------------------------------
        # Step 2: Simulate Passage of Time (Hold Expired in DB)
        # ------------------------------------------------------------------
        expired_time = datetime.now(timezone.utc) - timedelta(minutes=15)
        expired_str = expired_time.strftime("%Y-%m-%d %H:%M:%S")

        with db_session() as conn:
            conn.execute("UPDATE orders SET hold_expires_at = ? WHERE id = ?", (expired_str, order_id))

        # ------------------------------------------------------------------
        # Step 3: Payment Attempt on Expired Hold is Blocked by State Guard
        # ------------------------------------------------------------------
        stale_pay_res = self.client.post(f"/orders/{order_id}/process_payment")
        self.assertEqual(stale_pay_res.status_code, 400)
        self.assertIn("expired", stale_pay_res.get_json()["error"].lower())

        # Assert status transitioned to 'expired'
        expired_order = self.get_order(order_id)
        self.assertEqual(expired_order["status"], "expired")

        # ------------------------------------------------------------------
        # Step 4: Verify Worker expire_stale_holds() Handles Un-checked-out Holds
        # ------------------------------------------------------------------
        # Create second stale order that was completely abandoned without any API call
        claim_res_2 = self.client.post("/claim_seats", json={
            "user_id": "fiona_abandon",
            "seats": ["E2"]
        })
        order_id_2 = claim_res_2.get_json()["order"]["id"]

        with db_session() as conn:
            conn.execute("UPDATE orders SET hold_expires_at = ? WHERE id = ?", (expired_str, order_id_2))

        # Invoke background worker's expiration logic
        expired_count = worker_instance.expire_stale_holds()
        self.assertGreaterEqual(expired_count, 1)

        order_2_data = self.get_order(order_id_2)
        self.assertEqual(order_2_data["status"], "expired")

        # ------------------------------------------------------------------
        # Step 5: Seats E1 and E2 Are Now Free for Other Customers
        # ------------------------------------------------------------------
        reclaim_res = self.client.post("/claim_seats", json={
            "user_id": "george_new",
            "seats": ["E1", "E2"]
        })
        self.assertEqual(reclaim_res.status_code, 201)
        self.assertEqual(reclaim_res.get_json()["order"]["status"], "held")

        # ------------------------------------------------------------------
        # Step 6: Verify Audit History for Expired Orders & Log Details
        # ------------------------------------------------------------------
        self.assert_status_history(order_id, [
            "held",
            "expired"
        ])
        self.assert_status_history(order_id_2, [
            "held",
            "expired"
        ])
        self.log_order_details(order_id, label="Expired via Guard Check on Checkout")
        self.log_order_details(order_id_2, label="Expired via Background Worker Sweep")

if __name__ == "__main__":
    unittest.main()
