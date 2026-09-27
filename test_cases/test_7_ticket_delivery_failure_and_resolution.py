"""
Test Case 7: Ticket Delivery Failure, Human Resolution, & Cancelled Order Closure
State Progressions:
1. Delivery failure & staff resolution to complete:
   held -> initialized -> payment_authorized -> ticket_delivery_failed -> needs_human_resolution -> complete
2. Delivery failure resolved via cancellation to closed:
   held -> initialized -> payment_authorized -> ticket_delivery_failed -> needs_human_resolution -> cancelled -> closed
3. Delivery failure resolved via cancellation & refund:
   held -> initialized -> payment_authorized -> ticket_delivery_failed -> needs_human_resolution -> cancelled -> refunded -> closed
4. Unpaid order cancelled and closed:
   held -> cancelled -> closed
"""
import unittest
from test_cases.base_test import BaseTestCase

class TestTicketDeliveryFailureAndResolution(BaseTestCase):
    def test_delivery_failure_and_resolution_to_complete(self):
        self.log_test_description(
            title="Test Case 7A: Ticket Delivery Failure and Resolution to Complete",
            state_path="held -> initialized -> payment_authorized -> ticket_delivery_failed -> needs_human_resolution -> complete",
            description="Exercises delivery failure (bouncing email): order advances to payment_authorized. Delivery fails, transitioning to ticket_delivery_failed then needs_human_resolution. Customer updates email, staff re-delivers, and order reaches complete."
        )

        # 1. Claim seats -> 'held'
        res_claim = self.client.post("/claim_seats", json={
            "user_id": "clark_kent",
            "seats": ["G1"]
        })
        self.assertEqual(res_claim.status_code, 201)
        order_id = res_claim.get_json()["order"]["id"]

        # 2. Update with bouncing/invalid email
        res_init = self.client.patch(f"/orders/{order_id}", json={
            "contact_info": {
                "fname": "Clark",
                "lname": "Kent",
                "email": "clark@bounce.invalid",  # Trigger delivery failure
                "phone": "555-0700",
                "addr_street": "344 Clinton St",
                "addr_city": "Metropolis",
                "addr_state": "NY",
                "addr_zip": "10001"
            },
            "payment_info": {
                "cc_name": "Clark Kent",
                "cc_number": "4111111111114242",
                "cc_expiry": "10/28"
            }
        })
        self.assertEqual(res_init.status_code, 200)

        # 3. Authorize payment -> 'payment_authorized'
        res_pay = self.client.post(f"/orders/{order_id}/process_payment")
        self.assertEqual(res_pay.status_code, 200)
        self.assertEqual(res_pay.get_json()["order"]["status"], "payment_authorized")

        # 4. Attempt delivery -> Fails due to invalid/bounce email -> transitions to ticket_delivery_failed -> needs_human_resolution
        res_del = self.client.post(f"/orders/{order_id}/deliver_tickets")
        self.assertEqual(res_del.status_code, 502)
        data_del = res_del.get_json()
        self.assertEqual(data_del["order"]["status"], "needs_human_resolution")

        # Verify audit history passed through ticket_delivery_failed to needs_human_resolution
        self.assert_status_history(order_id, [
            "held",
            "initialized",
            "payment_authorized",
            "ticket_delivery_failed",
            "needs_human_resolution"
        ])
        self.log_order_details(order_id, label="Delivery Failed - Escalated to Human Resolution")

        # 6. Verify paid seats are protected during human resolution
        res_conflict = self.client.post("/claim_seats", json={
            "user_id": "lex_luthor",
            "seats": ["G1"]
        })
        self.assertEqual(res_conflict.status_code, 409)

        # 7. Customer / Staff updates contact info with valid working email
        res_update = self.client.patch(f"/orders/{order_id}", json={
            "email": "clark.kent@dailyplanet.com"
        })
        self.assertEqual(res_update.status_code, 200)

        # 8. Staff re-triggers delivery -> succeeds and transitions needs_human_resolution -> complete
        res_retry = self.client.post(f"/orders/{order_id}/deliver_tickets")
        self.assertEqual(res_retry.status_code, 200)
        self.assertEqual(res_retry.get_json()["order"]["status"], "complete")

        # 9. Verify full audit trail
        self.assert_status_history(order_id, [
            "held",
            "initialized",
            "payment_authorized",
            "ticket_delivery_failed",
            "needs_human_resolution",
            "complete"
        ])
        self.log_order_details(order_id, label="Delivery Succeeded Post-Resolution")

    def test_delivery_failure_resolved_to_closed_via_cancelled(self):
        self.log_test_description(
            title="Test Case 7B: Delivery Failure Resolved to Closed via Cancelled",
            state_path="held -> initialized -> payment_authorized -> ticket_delivery_failed -> needs_human_resolution -> cancelled -> closed",
            description="Exercises staff aborting a delivery-failed order: routes through cancelled to reach terminal closed status."
        )

        # 1. Claim seats & authorize
        res_claim = self.client.post("/claim_seats", json={"user_id": "bruce_banner", "seats": ["G2"]})
        order_id = res_claim.get_json()["order"]["id"]

        self.client.patch(f"/orders/{order_id}", json={
            "contact_info": {"fname": "Bruce", "lname": "Banner", "email": "bruce@banner.com", "phone": "555-0800", "addr_street": "1 Culver St", "addr_city": "Dayton", "addr_state": "OH", "addr_zip": "45401"},
            "payment_info": {"cc_name": "Bruce Banner", "cc_number": "4111111111114242", "cc_expiry": "03/29"}
        })
        self.client.post(f"/orders/{order_id}/process_payment")

        # 2. Fail delivery with explicit simulation flag
        res_fail = self.client.post(f"/orders/{order_id}/deliver_tickets", json={"simulate_failure": True})
        self.assertEqual(res_fail.status_code, 502)
        self.assertEqual(res_fail.get_json()["order"]["status"], "needs_human_resolution")

        # 3. Invariant: Attempting to close NHR order directly via /close must fail (no implicit jump to cancelled)
        res_close_invalid = self.client.post(f"/orders/{order_id}/close")
        self.assertEqual(res_close_invalid.status_code, 400)
        self.assertIn("Invalid state transition", res_close_invalid.get_json()["error"])

        # 4. Staff resolves NHR to cancelled
        res_resolve = self.client.post(f"/orders/{order_id}/resolve", json={
            "target_state": "cancelled",
            "reason": "Customer accepted store credit settlement"
        })
        self.assertEqual(res_resolve.status_code, 200)
        self.assertEqual(res_resolve.get_json()["order"]["status"], "cancelled")

        # 5. Now that order is cancelled, staff closes the cancelled order via /close
        res_close = self.client.post(f"/orders/{order_id}/close", json={
            "reason": "Administrative archival after store credit settlement"
        })
        self.assertEqual(res_close.status_code, 200)
        self.assertEqual(res_close.get_json()["order"]["status"], "closed")

        # 6. Verify audit history shows transition through cancelled before closed
        self.assert_status_history(order_id, [
            "held",
            "initialized",
            "payment_authorized",
            "ticket_delivery_failed",
            "needs_human_resolution",
            "cancelled",
            "closed"
        ])

    def test_delivery_failure_resolved_to_refunded_via_cancelled(self):
        self.log_test_description(
            title="Test Case 7C: Delivery Failure Refunded via Cancelled",
            state_path="held -> initialized -> payment_authorized -> ticket_delivery_failed -> needs_human_resolution -> cancelled -> refunded -> closed",
            description="Exercises refunding an order from needs_human_resolution: routes through cancelled to refunded then closed."
        )

        # 1. Claim seats & authorize
        res_claim = self.client.post("/claim_seats", json={"user_id": "barry_allen", "seats": ["G4"]})
        order_id = res_claim.get_json()["order"]["id"]

        self.client.patch(f"/orders/{order_id}", json={
            "contact_info": {"fname": "Barry", "lname": "Allen", "email": "barry@flash.invalid", "phone": "555-0900", "addr_street": "100 Speed Way", "addr_city": "Central City", "addr_state": "MO", "addr_zip": "64101"},
            "payment_info": {"cc_name": "Barry Allen", "cc_number": "4111111111114242", "cc_expiry": "06/29"}
        })
        self.client.post(f"/orders/{order_id}/process_payment")

        # 2. Fail delivery -> needs_human_resolution
        res_fail = self.client.post(f"/orders/{order_id}/deliver_tickets", json={"simulate_failure": True})
        self.assertEqual(res_fail.status_code, 502)
        self.assertEqual(res_fail.get_json()["order"]["status"], "needs_human_resolution")

        # 3. Customer / Staff requests refund via /refund endpoint
        res_refund = self.client.post(f"/orders/{order_id}/refund", json={
            "reason": "Customer requested full refund due to delivery failure"
        })
        self.assertEqual(res_refund.status_code, 200)
        self.assertEqual(res_refund.get_json()["order"]["status"], "closed")

        # 4. Verify audit history: refunded is strictly down the pathway from cancelled
        self.assert_status_history(order_id, [
            "held",
            "initialized",
            "payment_authorized",
            "ticket_delivery_failed",
            "needs_human_resolution",
            "cancelled",
            "refunded",
            "closed"
        ])

    def test_cancelled_order_transition_to_closed(self):
        self.log_test_description(
            title="Test Case 7D: Unpaid Cancelled Order Closed",
            state_path="held -> cancelled -> closed",
            description="Exercises closing an unpaid cancelled order: order is held, cancelled, then administratively closed via POST /orders/<id>/close."
        )

        # 1. Claim seat -> 'held'
        res_claim = self.client.post("/claim_seats", json={"user_id": "tony_stark", "seats": ["G3"]})
        order_id = res_claim.get_json()["order"]["id"]

        # 2. Cancel order -> 'cancelled'
        res_cancel = self.client.post("/cancel_order", json={"order_id": order_id})
        self.assertEqual(res_cancel.status_code, 200)
        self.assertEqual(res_cancel.get_json()["order"]["status"], "cancelled")

        # 3. Close cancelled order via /orders/<id>/close -> 'closed'
        res_close = self.client.post(f"/orders/{order_id}/close", json={
            "reason": "Administrative archival of cancelled order"
        })
        self.assertEqual(res_close.status_code, 200)
        self.assertEqual(res_close.get_json()["order"]["status"], "closed")

        # 4. Verify audit history
        self.assert_status_history(order_id, [
            "held",
            "cancelled",
            "closed"
        ])

if __name__ == "__main__":
    unittest.main()
