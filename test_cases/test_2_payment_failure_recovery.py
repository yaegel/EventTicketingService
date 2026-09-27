"""
Test Case 2: Payment Decline, Recovery, and Fulfillment
State Progression: held -> initialized -> payment_failed -> held -> initialized -> payment_authorized -> complete

Walkthrough:
1. Customer reserves seat ('held').
2. Customer supplies contact info and an invalid card (ending in '0000' = Insufficient Funds), advancing to 'initialized'.
3. Payment authorization fails; system transitions order to 'payment_failed' (HTTP 402).
4. Customer fixes card details via payment_info endpoint, stepping back to 'held' to re-verify inventory and pricing.
5. Customer re-initializes and completes authorization with valid card.
6. Order progresses to 'complete'.
7. Verifies full recovery trail recorded in audit history.
"""
import unittest
from test_cases.base_test import BaseTestCase

class TestPaymentFailureRecovery(BaseTestCase):
    def test_payment_failure_and_recovery_flow(self):
        self.log_test_description(
            title="Test Case 2: Payment Decline, Recovery, and Fulfillment",
            state_path="held -> initialized -> payment_failed -> held -> initialized -> payment_authorized -> complete",
            description="Exercises card decline handling (insufficient funds on card ending in 0000), state transition to payment_failed, customer updating card details to 4242, stepping back to held, re-initializing, and successfully fulfilling the order."
        )

        # ------------------------------------------------------------------
        # Step 1: Claim Seat -> 'held'
        # ------------------------------------------------------------------
        claim_res = self.client.post("/claim_seats", json={
            "user_id": "bob_builder",
            "seats": ["B1"]
        })
        self.assertEqual(claim_res.status_code, 201)
        order_id = claim_res.get_json()["order"]["id"]
        self.assertEqual(claim_res.get_json()["order"]["status"], "held")

        # ------------------------------------------------------------------
        # Step 2: Initialize with Declining Card (...0000 = Insufficient Funds)
        # ------------------------------------------------------------------
        init_res = self.client.patch(f"/orders/{order_id}", json={
            "contact_info": {
                "fname": "Bob",
                "lname": "Builder",
                "email": "bob@example.com",
                "phone": "555-0200",
                "addr_street": "456 Oak St",
                "addr_city": "Denver",
                "addr_state": "CO",
                "addr_zip": "80201"
            },
            "payment_info": {
                "cc_name": "Bob Builder",
                "cc_number": "4111111111110000",  # Triggers Insufficient Funds in Stub Gateway
                "cc_expiry": "11/27"
            }
        })
        self.assertEqual(init_res.status_code, 200)
        self.assertEqual(init_res.get_json()["order"]["status"], "initialized")

        # ------------------------------------------------------------------
        # Step 3: Attempt Payment -> Transitions initialized -> 'payment_failed' (HTTP 402)
        # ------------------------------------------------------------------
        pay_res = self.client.post(f"/orders/{order_id}/process_payment")
        self.assertEqual(pay_res.status_code, 402)
        data_failed = pay_res.get_json()
        self.assertEqual(data_failed["order"]["status"], "payment_failed")
        self.assertFalse(data_failed["payment"]["success"])
        self.assertIn("Insufficient funds", data_failed["error"])

        # ------------------------------------------------------------------
        # Step 4: Customer Updates Card Information & Transitions Back to 'initialized'
        # ------------------------------------------------------------------
        update_card_res = self.client.patch(f"/orders/{order_id}", json={
            "cc_name": "Bob Builder",
            "cc_number": "4111111111114242",  # Valid working card
            "cc_expiry": "11/29"
        })
        self.assertEqual(update_card_res.status_code, 200)
        self.assertEqual(update_card_res.get_json()["order"]["status"], "initialized")

        # ------------------------------------------------------------------
        # Step 5: Authorize with New Card -> Transitions held -> initialized -> 'payment_authorized'
        # ------------------------------------------------------------------
        re_pay_res = self.client.post(f"/orders/{order_id}/process_payment")
        self.assertEqual(re_pay_res.status_code, 200)
        data_re_pay = re_pay_res.get_json()
        self.assertEqual(data_re_pay["order"]["status"], "payment_authorized")
        self.assertTrue(data_re_pay["payment"]["success"])

        # ------------------------------------------------------------------
        # Step 6: Complete Order -> 'complete'
        # ------------------------------------------------------------------
        comp_res = self.client.post(f"/orders/{order_id}/deliver_tickets")
        self.assertEqual(comp_res.status_code, 200)
        self.assertEqual(comp_res.get_json()["order"]["status"], "complete")

        # ------------------------------------------------------------------
        # Step 8: Verify Full Recovery Audit Sequence & Log Details
        # ------------------------------------------------------------------
        self.assert_status_history(order_id, [
            "held",
            "initialized",
            "payment_failed",
            "held",
            "initialized",
            "payment_authorized",
            "complete"
        ])
        self.log_order_details(order_id, label="Payment Decline & Recovery Completed")

if __name__ == "__main__":
    unittest.main()
