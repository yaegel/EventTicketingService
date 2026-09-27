"""
Test Case 6: Worker-Orchestrated Order Fulfillment Pipeline
State Progression:
- Happy path: held -> initialized -> (worker calls /process_payment) -> 
    payment_authorized -> (worker calls /deliver_tickets) -> complete
- Decline path: held -> initialized -> (worker calls /process_payment) -> payment_failed

Walkthrough:
1. Customer reserves seats ('held') and inputs contact & valid credit card info ('initialized').
2. Background worker executes process_oldest_record():
   - Calls POST /orders/<id>/process_payment (authorizing through StubPaymentGateway)
   - Calls POST /orders/<id>/deliver_tickets (creating tickets & dispatching to complete order)
   - Updates orders.processed_at in database
3. Asserts order is in 'complete' status, tickets are generated, and delivery timestamp is set.
4. Also verifies that when an invalid card is provided, the worker transitions order to 'payment_failed'
   and halts ticket generation.
"""
import unittest
from test_cases.base_test import BaseTestCase
from worker import worker_instance

class TestWorkerProcessing(BaseTestCase):
    def test_worker_orchestrated_fulfillment(self):
        self.log_test_description(
            title="Test Case 6A: Worker-Orchestrated Fulfillment (Happy Path)",
            state_path="held -> initialized -> (Worker: payment -> delivery) -> complete",
            description="Exercises worker pipeline: customer initializes order. Worker calls process_payment endpoint with StubPaymentGateway and deliver_tickets, completing the order and stamping processed_at."
        )

        worker_instance.set_app(self.app)

        # 1. Claim seats
        res_claim = self.client.post("/claim_seats", json={
            "user_id": "grace_hopper",
            "seats": ["F1", "F2"]
        })
        self.assertEqual(res_claim.status_code, 201)
        order_id = res_claim.get_json()["order"]["id"]

        # 2. Provide contact and payment details
        res_init = self.client.patch(f"/orders/{order_id}", json={
            "contact_info": {
                "fname": "Grace",
                "lname": "Hopper",
                "email": "grace@navy.mil",
                "phone": "555-0600",
                "addr_street": "100 Admiral Way",
                "addr_city": "Arlington",
                "addr_state": "VA",
                "addr_zip": "22202"
            },
            "payment_info": {
                "cc_name": "Grace Hopper",
                "cc_number": "4111111111114242",
                "cc_expiry": "09/30"
            }
        })
        self.assertEqual(res_init.status_code, 200)

        # 3. Worker Turn 1: picks up 'initialized' order and calls payment API
        processed_turn_1 = worker_instance.process_oldest_record()
        self.assertTrue(processed_turn_1)

        # Verify payment API transitioned state in DB to 'payment_authorized', but tickets not yet generated
        order_mid = self.get_order(order_id)
        self.assertEqual(order_mid["status"], "payment_authorized")
        self.assertIsNotNone(order_mid["payment_info"]["payment_transaction_id"])
        self.assertIsNone(order_mid["processed_at"])
        self.assertIsNone(order_mid["tickets"])

        # 4. Worker Turn 2: picks up 'payment_authorized' order from DB and generates tickets + delivers
        processed_turn_2 = worker_instance.process_oldest_record()
        self.assertTrue(processed_turn_2)

        # 5. Assert final order details
        order = self.get_order(order_id)
        self.assertEqual(order["status"], "complete")
        self.assertIsNotNone(order["processed_at"])
        self.assertIsNotNone(order["delivered_at"])
        self.assertIsNotNone(order["payment_info"]["payment_transaction_id"])
        self.assertIsNotNone(order["tickets"])
        self.assertEqual(len(order["tickets"]), 2)

        # Verify no further work remains
        self.assertFalse(worker_instance.process_oldest_record())

        # Verify tickets endpoint
        res_tkt = self.client.get(f"/orders/{order_id}/tickets")
        self.assertEqual(res_tkt.status_code, 200)
        self.assertEqual(res_tkt.get_json()["count"], 2)

        # 5. Verify audit history sequence
        self.assert_status_history(order_id, [
            "held",
            "initialized",
            "payment_authorized",
            "complete"
        ])
        self.log_order_details(order_id, label="Worker Fulfillment Completed")

    def test_worker_handles_payment_decline(self):
        self.log_test_description(
            title="Test Case 6B: Worker Handles Payment Decline",
            state_path="held -> initialized -> (Worker calls payment) -> payment_failed",
            description="Exercises worker pipeline on declined card: worker calls /process_payment which receives decline from StubPaymentGateway. Order transitions to payment_failed and ticket generation is not executed."
        )

        worker_instance.set_app(self.app)

        # 1. Claim seat
        res_claim = self.client.post("/claim_seats", json={
            "user_id": "hal_9000",
            "seats": ["F3"]
        })
        self.assertEqual(res_claim.status_code, 201)
        order_id = res_claim.get_json()["order"]["id"]

        # 2. Provide details with declining card ending in 0000
        res_init = self.client.patch(f"/orders/{order_id}", json={
            "contact_info": {
                "fname": "Hal",
                "lname": "NineThousand",
                "email": "hal@discovery.one",
                "phone": "555-0900",
                "addr_street": "1 Discovery Orbit",
                "addr_city": "Jupiter",
                "addr_state": "FL",
                "addr_zip": "32920"
            },
            "payment_info": {
                "cc_name": "Hal 9000",
                "cc_number": "4111111111110000",  # Declines with Insufficient Funds
                "cc_expiry": "01/30"
            }
        })
        self.assertEqual(res_init.status_code, 200)

        # 3. Worker runs processing
        processed = worker_instance.process_oldest_record()
        self.assertTrue(processed)

        # 4. Assert order transitioned to payment_failed and no tickets were generated
        order = self.get_order(order_id)
        self.assertEqual(order["status"], "payment_failed")
        self.assertIsNone(order["processed_at"])
        self.assertIsNone(order["delivered_at"])
        self.assertIsNone(order["tickets"])

        # 5. Subsequent worker turn: payment_failed order is not picked up
        self.assertFalse(worker_instance.process_oldest_record())

        self.assert_status_history(order_id, [
            "held",
            "initialized",
            "payment_failed"
        ])
        self.log_order_details(order_id, label="Worker Payment Decline Handled")

    def test_concurrent_workers_atomic_locking(self):
        """
        Verify that multiple concurrent workers never process the same order simultaneously.
        Creates 2 distinct orders and processes them with 2 concurrent worker threads.
        Asserts that both orders are fulfilled without conflict.
        """
        self.log_test_description(
            title="Test Case 6C: Concurrent Workers Atomic Record Locking",
            state_path="held -> initialized -> (2 Concurrent Workers) -> complete",
            description="Exercises atomic claim locking: two orders are initialized simultaneously. Two separate worker threads poll concurrently, ensuring each claims a unique order without collisions or double-processing."
        )

        worker_instance.set_app(self.app)

        # 1. Create two distinct orders
        res_a = self.client.post("/claim_seats", json={"user_id": "user_a", "seats": ["H1"]})
        order_a = res_a.get_json()["order"]["id"]
        self.client.patch(f"/orders/{order_a}", json={
            "contact_info": {
                "fname": "User", "lname": "A", "email": "a@example.com", "phone": "555-0101",
                "addr_street": "1 Main St", "addr_city": "Metropolis", "addr_state": "NY", "addr_zip": "10001"
            },
            "payment_info": {"cc_name": "User A", "cc_number": "4111111111114242", "cc_expiry": "12/28"}
        })

        res_b = self.client.post("/claim_seats", json={"user_id": "user_b", "seats": ["H2"]})
        order_b = res_b.get_json()["order"]["id"]
        self.client.patch(f"/orders/{order_b}", json={
            "contact_info": {
                "fname": "User", "lname": "B", "email": "b@example.com", "phone": "555-0102",
                "addr_street": "2 Main St", "addr_city": "Gotham", "addr_state": "NJ", "addr_zip": "07001"
            },
            "payment_info": {"cc_name": "User B", "cc_number": "4111111111114242", "cc_expiry": "12/28"}
        })

        import threading
        results = []

        def worker_task(worker_name):
            turns_processed = 0
            while turns_processed < 2 and worker_instance.process_oldest_record(worker_name=worker_name):
                turns_processed += 1
            results.append((worker_name, turns_processed))

        t1 = threading.Thread(target=worker_task, args=("WorkerThread-1",))
        t2 = threading.Thread(target=worker_task, args=("WorkerThread-2",))
        t1.start()
        t2.start()
        t1.join(timeout=10.0)
        t2.join(timeout=10.0)

        # Drain any remaining fulfillment stages
        while worker_instance.process_oldest_record():
            pass

        final_a = self.get_order(order_a)
        final_b = self.get_order(order_b)

        self.assertEqual(final_a["status"], "complete")
        self.assertEqual(final_b["status"], "complete")
        self.assertIsNotNone(final_a["processed_at"])
        self.assertIsNotNone(final_b["processed_at"])
        self.assertEqual(len(final_a["tickets"]), 1)
        self.assertEqual(len(final_b["tickets"]), 1)

        self.assert_status_history(order_a, ["held", "initialized", "payment_authorized", "complete"])
        self.assert_status_history(order_b, ["held", "initialized", "payment_authorized", "complete"])

        self.log_order_details(order_a, label="Concurrent Worker Processing - Order A")
        self.log_order_details(order_b, label="Concurrent Worker Processing - Order B")

if __name__ == "__main__":
    unittest.main()
