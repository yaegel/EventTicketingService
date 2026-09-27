"""
Test Case 3: Pre-Payment Voluntary Cancellation & Seat Release
State Progression: held -> cancelled

Walkthrough:
1. Customer A reserves seats ('held').
2. Customer B attempts to claim the same seats and is rejected with HTTP 409 Conflict.
3. Customer A voluntarily cancels the order via /cancel_order.
4. Order transitions to terminal 'cancelled' state and rejects further modifications.
5. The held seats are immediately freed; Customer B successfully claims the seats.
6. Verifies audit history reflects clean transition: ['held', 'cancelled'].
"""
import unittest
from test_cases.base_test import BaseTestCase

class TestVoluntaryCancellation(BaseTestCase):
    def test_voluntary_cancellation_and_seat_release(self):
        self.log_test_description(
            title="Test Case 3: Pre-Payment Voluntary Cancellation & Seat Release",
            state_path="held -> cancelled",
            description="Exercises pre-payment voluntary cancellation: Customer A holds seats, Customer B is blocked with HTTP 409 Conflict. Customer A cancels order via /cancel_order. Terminal state is verified, and Customer B successfully books the released seats."
        )

        # ------------------------------------------------------------------
        # Step 1: Customer A reserves seats C1 & C2 -> 'held'
        # ------------------------------------------------------------------
        res_a = self.client.post("/claim_seats", json={
            "user_id": "customer_a",
            "seats": ["C1", "C2"]
        })
        self.assertEqual(res_a.status_code, 201)
        order_id_a = res_a.get_json()["order"]["id"]
        self.assertEqual(res_a.get_json()["order"]["status"], "held")

        # ------------------------------------------------------------------
        # Step 2: Customer B attempts to claim seat C1 -> HTTP 409 Conflict
        # ------------------------------------------------------------------
        res_b_conflict = self.client.post("/claim_seats", json={
            "user_id": "customer_b",
            "seats": ["C1"]
        })
        self.assertEqual(res_b_conflict.status_code, 409)
        self.assertIn("conflicting_seats", res_b_conflict.get_json())
        self.assertIn("C1", res_b_conflict.get_json()["conflicting_seats"])

        # ------------------------------------------------------------------
        # Step 3: Customer A cancels order via /cancel_order -> 'cancelled'
        # ------------------------------------------------------------------
        res_cancel = self.client.post("/cancel_order", json={"order_id": order_id_a})
        self.assertEqual(res_cancel.status_code, 200)
        data_cancel = res_cancel.get_json()
        self.assertEqual(data_cancel["order"]["status"], "cancelled")

        # ------------------------------------------------------------------
        # Step 4: Verify Terminal State Invariants (cannot advance cancelled order)
        # ------------------------------------------------------------------
        res_pay_dead = self.client.post(f"/orders/{order_id_a}/process_payment")
        self.assertEqual(res_pay_dead.status_code, 400)
        self.assertIn("error", res_pay_dead.get_json())

        # ------------------------------------------------------------------
        # Step 5: Seats are freed; Customer B can now claim C1 and C2
        # ------------------------------------------------------------------
        res_b_success = self.client.post("/claim_seats", json={
            "user_id": "customer_b",
            "seats": ["C1", "C2"]
        })
        self.assertEqual(res_b_success.status_code, 201)
        order_id_b = res_b_success.get_json()["order"]["id"]
        self.assertNotEqual(order_id_a, order_id_b)
        self.assertEqual(res_b_success.get_json()["order"]["status"], "held")

        # ------------------------------------------------------------------
        # Step 6: Verify Audit History & Log Details
        # ------------------------------------------------------------------
        self.assert_status_history(order_id_a, [
            "held",
            "cancelled"
        ])
        self.log_order_details(order_id_a, label="Cancelled Order (Customer A)")
        self.log_order_details(order_id_b, label="Newly Claimed Order (Customer B)")

if __name__ == "__main__":
    unittest.main()
