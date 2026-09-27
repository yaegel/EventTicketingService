"""
Test Case 4: Real-World Refund, Cancellation, & Escalation Flows
State Progressions:
- Successful Refund: held -> initialized -> payment_authorized -> complete -> cancelled -> refunded -> closed
- Failed Refund:     held -> initialized -> payment_authorized -> complete -> cancelled -> refund_failed -> needs_human_resolution
- Unpaid Decline:    held -> initialized -> payment_failed -> cancelled

Walkthrough:
1. Completed order cancellation & refund:
   - Order advances through checkout to 'complete'.
   - Customer initiates refund via POST /orders/<id>/refund.
   - Transitions complete -> cancelled -> refunded -> closed.
   - Tickets are marked 'void' and seats released.
2. Failed refund escalation:
   - Order completed with card ending in 8888 (issuer rejects return of funds).
   - Refund fails at payment gateway.
   - Transitions complete -> cancelled -> refund_failed -> needs_human_resolution.
   - Tickets are voided and order flagged for customer service staff.
3. Payment failed voluntary cancellation:
   - Customer card is declined ('payment_failed').
   - Customer realizes they do not have sufficient funds and cancels via /cancel_order.
   - Transitions payment_failed -> cancelled.
4. Invariant checks:
   - Orders in 'payment_authorized' cannot transition to 'refunded', 'needs_human_resolution', or 'cancelled'.
"""
import unittest
from test_cases.base_test import BaseTestCase

class TestRefundCancellation(BaseTestCase):
    def test_successful_refund_flow(self):
        self.log_test_description(
            title="Test Case 4A: Successful Post-Completion Refund Flow",
            state_path="held -> initialized -> payment_authorized -> complete -> cancelled -> refunded -> closed",
            description="Exercises real-world refund lifecycle: order reaches complete. When customer cancels and refunds, order transitions through cancelled -> refunded -> closed. Tickets are voided and seats are freed."
        )

        # ------------------------------------------------------------------
        # Step 1: Claim Seat -> 'held'
        # ------------------------------------------------------------------
        # ------------------------------------------------------------------
        # Step 1: Claim, pay, and complete order
        # ------------------------------------------------------------------
        order_id = self.create_completed_order(
            user_id="diana_prince",
            seats=["D1"],
            fname="Diana",
            lname="Prince",
            email="diana@example.com"
        )

        # ------------------------------------------------------------------
        # Step 5: Process Refund via /orders/<id>/refund -> cancelled -> refunded -> closed
        # ------------------------------------------------------------------
        refund_res = self.client.post(f"/orders/{order_id}/refund", json={
            "reason": "Customer requested cancellation prior to event"
        })
        self.assertEqual(refund_res.status_code, 200)
        data_refund = refund_res.get_json()
        self.assertTrue(data_refund["refund"]["success"])
        # Terminal status must be 'closed'
        self.assertEqual(data_refund["order"]["status"], "closed")

        # ------------------------------------------------------------------
        # Step 6: Verify Full Audit History Sequence
        # ------------------------------------------------------------------
        self.assert_status_history(order_id, [
            "held",
            "initialized",
            "payment_authorized",
            "complete",
            "cancelled",
            "refunded",
            "closed"
        ])
        self.log_order_details(order_id, label="Refund Processed & Order Closed")

        # ------------------------------------------------------------------
        # Step 7: Verify Tickets are Marked 'void'
        # ------------------------------------------------------------------
        tickets = data_refund["order"].get("tickets")
        if tickets:
            for tkt in tickets:
                self.assertEqual(tkt["status"], "void")

        # ------------------------------------------------------------------
        # Step 8: Verify Released Seat D1 Can Be Reclaimed
        # ------------------------------------------------------------------
        reclaim_res = self.client.post("/claim_seats", json={
            "user_id": "clark_kent",
            "seats": ["D1"]
        })
        self.assertEqual(reclaim_res.status_code, 201)
        self.assertEqual(reclaim_res.get_json()["order"]["status"], "held")

    def test_failed_refund_escalation_flow(self):
        self.log_test_description(
            title="Test Case 4B: Failed Refund Escalation Flow",
            state_path="held -> initialized -> payment_authorized -> complete -> cancelled -> refund_failed -> needs_human_resolution",
            description="Exercises failed refund flow: order is completed with card 8888. When refund is initiated, issuer declines return of funds. Order transitions through cancelled -> refund_failed -> needs_human_resolution for staff resolution."
        )

        # 1. Claim, pay with card ending in '8888', and complete order
        order_d2_id = self.create_completed_order(
            user_id="bruce_wayne",
            seats=["D2"],
            cc_number="4111111111118888",
            fname="Bruce",
            lname="Wayne",
            email="bruce@wayne.com"
        )

        # 5. Attempt refund via POST /orders/<id>/refund -> issuer declines -> HTTP 402
        refund_res = self.client.post(f"/orders/{order_d2_id}/refund", json={
            "reason": "VIP customer requesting cancellation"
        })
        self.assertEqual(refund_res.status_code, 402)
        data_refund = refund_res.get_json()
        self.assertFalse(data_refund["refund"]["success"])
        self.assertEqual(data_refund["order"]["status"], "needs_human_resolution")

        # 6. Verify audit history sequence reflects cancelled -> refund_failed -> needs_human_resolution
        self.assert_status_history(order_d2_id, [
            "held",
            "initialized",
            "payment_authorized",
            "complete",
            "cancelled",
            "refund_failed",
            "needs_human_resolution"
        ])
        self.log_order_details(order_d2_id, label="Failed Refund Escalated to Human Resolution")

        # 7. Verify seat D2 was released despite failed refund
        reclaim_d2 = self.client.post("/claim_seats", json={
            "user_id": "barry_allen",
            "seats": ["D2"]
        })
        self.assertEqual(reclaim_d2.status_code, 201)
        self.assertEqual(reclaim_d2.get_json()["order"]["status"], "held")

    def test_payment_failed_to_cancelled_flow(self):
        self.log_test_description(
            title="Test Case 4C: Payment Failed Order Cancelled by User",
            state_path="held -> initialized -> payment_failed -> cancelled",
            description="Exercises user cancelling order after payment decline: user realizes they lack funds and cancels via /cancel_order, cleanly transitioning from payment_failed to cancelled and freeing seats."
        )

        # 1. Claim seat D3
        claim_res = self.client.post("/claim_seats", json={
            "user_id": "arthur_curry",
            "seats": ["D3"]
        })
        self.assertEqual(claim_res.status_code, 201)
        order_id = claim_res.get_json()["order"]["id"]

        # 2. Update with card ending in '0000' (insufficient funds)
        self.client.patch(f"/orders/{order_id}", json={
            "contact_info": {
                "fname": "Arthur",
                "lname": "Curry",
                "email": "arthur@atlantis.gov",
                "phone": "555-0400",
                "addr_street": "1 Ocean Ave",
                "addr_city": "Amnesty Bay",
                "addr_state": "ME",
                "addr_zip": "04001"
            },
            "payment_info": {
                "cc_name": "Arthur Curry",
                "cc_number": "4111111111110000",
                "cc_expiry": "04/28"
            }
        })

        # 3. Payment authorization fails (HTTP 402) -> payment_failed
        pay_res = self.client.post(f"/orders/{order_id}/process_payment")
        self.assertEqual(pay_res.status_code, 402)
        self.assertEqual(pay_res.get_json()["order"]["status"], "payment_failed")

        # 4. User realizes lack of funds and cancels via /cancel_order
        cancel_res = self.client.post("/cancel_order", json={"order_id": order_id})
        self.assertEqual(cancel_res.status_code, 200)
        self.assertEqual(cancel_res.get_json()["order"]["status"], "cancelled")

        # 5. Verify audit history
        self.assert_status_history(order_id, [
            "held",
            "initialized",
            "payment_failed",
            "cancelled"
        ])
        self.log_order_details(order_id, label="Payment Failed Order Cancelled")

        # 6. Verify seat D3 is freed
        reclaim_d3 = self.client.post("/claim_seats", json={
            "user_id": "victor_stone",
            "seats": ["D3"]
        })
        self.assertEqual(reclaim_d3.status_code, 201)
        self.assertEqual(reclaim_d3.get_json()["order"]["status"], "held")

    def test_payment_authorized_invariants(self):
        self.log_test_description(
            title="Test Case 4D: Payment Authorized Invariant Checks",
            state_path="payment_authorized cannot go to refunded, needs_human_resolution, or cancelled",
            description="Verifies that an order in payment_authorized status cannot be directly refunded, escalated, or cancelled before fulfillment completes."
        )

        # 1. Claim and bring order to payment_authorized
        claim_res = self.client.post("/claim_seats", json={
            "user_id": "hal_jordan",
            "seats": ["D4"]
        })
        order_id = claim_res.get_json()["order"]["id"]

        self.client.patch(f"/orders/{order_id}", json={
            "contact_info": {
                "fname": "Hal",
                "lname": "Jordan",
                "email": "hal@ferris.com",
                "phone": "555-0500",
                "addr_street": "1 Ferris Lane",
                "addr_city": "Coast City",
                "addr_state": "CA",
                "addr_zip": "90210"
            },
            "payment_info": {
                "cc_name": "Hal Jordan",
                "cc_number": "4111111111114242",
                "cc_expiry": "07/29"
            }
        })

        pay_res = self.client.post(f"/orders/{order_id}/process_payment")
        self.assertEqual(pay_res.status_code, 200)
        self.assertEqual(pay_res.get_json()["order"]["status"], "payment_authorized")

        # 2. Attempt refund on payment_authorized -> rejected with HTTP 400
        refund_res = self.client.post(f"/orders/{order_id}/refund")
        self.assertEqual(refund_res.status_code, 400)
        self.assertIn("error", refund_res.get_json())

        # 3. Attempt direct cancel on payment_authorized -> rejected with HTTP 400
        cancel_res = self.client.post("/cancel_order", json={"order_id": order_id})
        self.assertEqual(cancel_res.status_code, 400)
        self.assertIn("error", cancel_res.get_json())

        # 4. Status must remain payment_authorized
        order = self.get_order(order_id)
        self.assertEqual(order["status"], "payment_authorized")

if __name__ == "__main__":
    unittest.main()
