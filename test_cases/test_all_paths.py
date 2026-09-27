"""
Master Test Suite Runner for Event Ticketing Service State Machine.
Executes all state path test cases:
1. Happy Path: held -> initialized -> payment_authorized -> complete
2. Payment Failure & Recovery: held -> initialized -> payment_failed -> held -> initialized -> payment_authorized -> complete
3. Voluntary Cancellation: held -> cancelled
4. Post-Completion Refund:
   - Success: held -> initialized -> payment_authorized -> complete -> cancelled -> refunded -> closed
   - Refund Failed: held -> initialized -> payment_authorized -> complete -> cancelled -> refund_failed -> needs_human_resolution
   - Payment Failed Cancellation: held -> initialized -> payment_failed -> cancelled
5. Seat Hold Expiration: held -> expired
6. Worker Fulfillment Pipeline:
   - 6A: held -> initialized -> payment_authorized -> complete
   - 6B: held -> initialized -> payment_failed
7. Ticket Delivery Failure & Human Resolution:
   - 7A: payment_authorized -> ticket_delivery_failed -> needs_human_resolution -> complete
   - 7B: payment_authorized -> ticket_delivery_failed -> needs_human_resolution -> cancelled -> closed
   - 7C: payment_authorized -> ticket_delivery_failed -> needs_human_resolution -> cancelled -> refunded -> closed
   - 7D: held -> cancelled -> closed
"""
import unittest
from test_cases.test_1_happy_path import TestHappyPath
from test_cases.test_2_payment_failure_recovery import TestPaymentFailureRecovery
from test_cases.test_3_voluntary_cancellation import TestVoluntaryCancellation
from test_cases.test_4_refund_cancellation import TestRefundCancellation
from test_cases.test_5_hold_expiration import TestHoldExpiration
from test_cases.test_6_worker_processing import TestWorkerProcessing
from test_cases.test_7_ticket_delivery_failure_and_resolution import TestTicketDeliveryFailureAndResolution

def suite():
    test_suite = unittest.TestSuite()
    test_suite.addTest(unittest.makeSuite(TestHappyPath))
    test_suite.addTest(unittest.makeSuite(TestPaymentFailureRecovery))
    test_suite.addTest(unittest.makeSuite(TestVoluntaryCancellation))
    test_suite.addTest(unittest.makeSuite(TestRefundCancellation))
    test_suite.addTest(unittest.makeSuite(TestHoldExpiration))
    test_suite.addTest(unittest.makeSuite(TestWorkerProcessing))
    test_suite.addTest(unittest.makeSuite(TestTicketDeliveryFailureAndResolution))
    return test_suite

if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite())
    exit(0 if result.wasSuccessful() else 1)
