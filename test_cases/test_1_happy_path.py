"""
Test Case 1: The Happy Path (Complete Order Lifecycle)
State Progression: held -> initialized -> payment_authorized -> complete

Walkthrough:
1. Customer reserves seats ('held') with temporary TTL.
2. Customer provides complete contact info and payment details, transitioning to 'initialized'.
3. Customer advances checkout; payment gateway approves authorization ('payment_authorized').
4. Order checkout completes, delivering tickets ('complete').
5. Asserts that terminal 'complete' state rejects invalid cancellations.
6. Verifies complete chronological audit history in order_status.
"""
import unittest
from test_cases.base_test import BaseTestCase

class TestHappyPath(BaseTestCase):
    def test_happy_path_lifecycle(self):
        self.log_test_description(
            title="Test Case 1: The Happy Path (Complete Order Lifecycle)",
            state_path="held -> initialized -> payment_authorized -> complete",
            description="Exercises standard checkout progression: seat hold reservation, contact & billing initialization, payment authorization via gateway, and ticket completion. Verifies terminal complete state rejects cancellations."
        )

        # ------------------------------------------------------------------
        # Step 1: Claim Seats -> 'held'
        # ------------------------------------------------------------------
        claim_payload = {
            "user_id": "alice_smith",
            "seats": ["A1", "A2"]
        }
        res_claim = self.client.post("/claim_seats", json=claim_payload)
        self.assertEqual(res_claim.status_code, 201)
        data_claim = res_claim.get_json()
        order_id = data_claim["order"]["id"]

        self.assertEqual(data_claim["order"]["status"], "held")
        self.assertEqual(data_claim["order"]["seats"], ["A1", "A2"])
        self.assertIsNotNone(data_claim["order"]["held_at"])
        self.assertIsNotNone(data_claim["order"]["hold_expires_at"])

        # ------------------------------------------------------------------
        # Step 2: Provide Contact & Card Details -> 'held' (details attached)
        # ------------------------------------------------------------------
        checkout_init_payload = {
            "contact_info": {
                "fname": "Alice",
                "lname": "Smith",
                "email": "alice@example.com",
                "phone": "555-0100",
                "addr_street": "123 Main St",
                "addr_city": "Austin",
                "addr_state": "TX",
                "addr_zip": "78701"
            },
            "payment_info": {
                "cc_name": "Alice Smith",
                "cc_number": "4111111111114242",
                "cc_expiry": "12/28"
            }
        }
        res_init = self.client.patch(f"/orders/{order_id}", json=checkout_init_payload)
        self.assertEqual(res_init.status_code, 200)
        data_init = res_init.get_json()
        self.assertEqual(data_init["order"]["status"], "initialized")
        self.assertEqual(data_init["order"]["contact_info"]["fname"], "Alice")
        self.assertEqual(data_init["order"]["payment_info"]["cc_last4"], "4242")

        # ------------------------------------------------------------------
        # Step 3: Authorize Payment -> held -> initialized -> 'payment_authorized'
        # ------------------------------------------------------------------
        res_pay = self.client.post(f"/orders/{order_id}/process_payment")
        self.assertEqual(res_pay.status_code, 200)
        data_pay = res_pay.get_json()
        self.assertEqual(data_pay["order"]["status"], "payment_authorized")
        self.assertIsNotNone(data_pay["order"]["payment_info"]["payment_transaction_id"])
        self.assertTrue(data_pay["payment"]["success"])
        self.assertEqual(data_pay["payment"]["status"], "authorized")

        # ------------------------------------------------------------------
        # Step 4: Finalize Order -> deliver tickets (auto-generates) -> 'complete'
        # ------------------------------------------------------------------
        res_comp = self.client.post(f"/orders/{order_id}/deliver_tickets")
        self.assertEqual(res_comp.status_code, 200)
        data_comp = res_comp.get_json()
        self.assertEqual(data_comp["order"]["status"], "complete")

        # ------------------------------------------------------------------
        # Step 5: Verify Invariants (completed order cannot jump directly to closed)
        # ------------------------------------------------------------------
        res_close_invalid = self.client.post(f"/orders/{order_id}/close")
        self.assertEqual(res_close_invalid.status_code, 400)
        self.assertIn("Invalid state transition", res_close_invalid.get_json()["error"])

        # ------------------------------------------------------------------
        # Step 6: Verify Full Status History Sequence & Log Details
        # ------------------------------------------------------------------
        self.assert_status_history(order_id, [
            "held",
            "initialized",
            "payment_authorized",
            "complete"
        ])
        self.log_order_details(order_id, label="Happy Path Completed")

if __name__ == "__main__":
    unittest.main()
