"""
Stub payment gateway test harness.
Provides an in-memory implementation of PaymentGateway for testing and local development,
supporting canned modes, card number rule matching, queued responses, and request recording.
"""
from enum import Enum
from typing import Optional, List, Dict, Any
import uuid
from helpers.date_helpers import get_now_in_iso
from payment_gateway import PaymentGateway, PaymentRequest, PaymentResult, PaymentStatus

class StubMode(str, Enum):
    AUTO = "auto"                      # Evaluate card number rules or queued results
    ALWAYS_AUTHORIZE = "always_authorize"
    ALWAYS_DECLINE = "always_decline"
    ALWAYS_ERROR = "always_error"

class StubPaymentGateway(PaymentGateway):
    """
    In-memory stubbed payment harness for testing and local development.

    Features:
    1. Card number matching rules:
       - Ending in '0000': Declined (Insufficient funds)
       - Ending in '1111': Declined (Expired card)
       - Ending in '2222': Declined (Suspected fraud)
       - Ending in '3333': Declined (Invalid verification code)
       - Ending in '7777': Authorized on payment, Error on refund (Network timeout on return of funds)
       - Ending in '8888': Authorized on payment, Declined on refund (Issuer rejected return of funds)
       - Ending in '9999': Error on payment (Gateway network timeout)
       - Any other card: Successfully authorized and refundable
    2. Configurable modes (AUTO, ALWAYS_AUTHORIZE, ALWAYS_DECLINE, ALWAYS_ERROR).
    3. Call logging to inspect requests and assert on gateway interactions.
    """

    def __init__(self, mode: StubMode = StubMode.AUTO):
        self.mode = mode
        self.recorded_requests: List[PaymentRequest] = []
        self.recorded_transactions: Dict[str, PaymentResult] = {}
        self._queued_results: List[PaymentResult] = []

    def set_mode(self, mode: StubMode) -> None:
        """Configure test mode."""
        self.mode = mode

    def enqueue_result(self, result: PaymentResult) -> None:
        """Queue a predetermined result for the next authorization call."""
        self._queued_results.append(result)

    def clear_history(self) -> None:
        """Clear recorded requests and transactions."""
        self.recorded_requests.clear()
        self.recorded_transactions.clear()

    def reset(self) -> None:
        """Reset test harness to default AUTO mode with cleared state."""
        self.mode = StubMode.AUTO
        self.clear_history()
        self._queued_results.clear()

    def get_last_request(self) -> Optional[PaymentRequest]:
        """Return the most recently processed payment request."""
        return self.recorded_requests[-1] if self.recorded_requests else None

    def authorize(self, request: PaymentRequest) -> PaymentResult:
        """Process an authorization using configured rules or enqueued responses."""
        self.recorded_requests.append(request)

        # 1. Use enqueued result if available
        if self._queued_results:
            result = self._queued_results.pop(0)
            if result.transaction_id:
                self.recorded_transactions[result.transaction_id] = result
            return result

        # 2. Check explicit global test modes
        if self.mode == StubMode.ALWAYS_DECLINE:
            return PaymentResult(
                status=PaymentStatus.DECLINED,
                success=False,
                error_message="Card declined: Issuer declined transaction.",
                timestamp=get_now_in_iso()
            )

        if self.mode == StubMode.ALWAYS_ERROR:
            return PaymentResult(
                status=PaymentStatus.ERROR,
                success=False,
                error_message="Payment gateway error: Service unavailable."
            )

        if self.mode == StubMode.ALWAYS_AUTHORIZE:
            txn_id = f"txn_{uuid.uuid4().hex[:12]}"
            res = PaymentResult(
                status=PaymentStatus.AUTHORIZED,
                success=True,
                transaction_id=txn_id,
                authorization_code=f"AUTH_{uuid.uuid4().hex[:6].upper()}",
                timestamp=get_now_in_iso(),
            )
            self.recorded_transactions[txn_id] = res
            return res

        # 3. AUTO mode: Evaluate card number rules
        digits = "".join(filter(str.isdigit, request.cc_number or ""))
        last4 = digits[-4:] if len(digits) >= 4 else request.cc_last4

        if last4 == "0000":
            return PaymentResult(
                status=PaymentStatus.DECLINED,
                success=False,
                error_message="Card declined: Insufficient funds.",
                timestamp=get_now_in_iso()
            )
        elif last4 == "1111":
            return PaymentResult(
                status=PaymentStatus.DECLINED,
                success=False,
                error_message="Card declined: Expired card.",
                timestamp=get_now_in_iso()
            )
        elif last4 == "2222":
            return PaymentResult(
                status=PaymentStatus.DECLINED,
                success=False,
                error_message="Card declined: Suspected fraud.",
                timestamp=get_now_in_iso()
            )
        elif last4 == "3333":
            return PaymentResult(
                status=PaymentStatus.DECLINED,
                success=False,
                error_message="Card declined: Invalid card verification code.",
                timestamp=get_now_in_iso()
            )
        elif last4 == "9999":
            return PaymentResult(
                status=PaymentStatus.ERROR,
                success=False,
                error_message="Gateway connection error: Timeout communicating with payment network."
            )

        # Default: Successful authorization (note: card '8888' authorizes successfully, but fails refunds)
        txn_id = f"txn_{uuid.uuid4().hex[:12]}"
        auth_code = f"AUTH_{uuid.uuid4().hex[:6].upper()}"
        res = PaymentResult(
            status=PaymentStatus.AUTHORIZED,
            success=True,
            transaction_id=txn_id,
            authorization_code=auth_code,
            timestamp=get_now_in_iso(),
            raw_details={
                "amount_cents": request.amount_cents,
                "currency": request.currency,
                "cardholder": request.cc_name,
                "last4": last4
            }
        )
        self.recorded_transactions[txn_id] = res
        return res

    def capture(self, transaction_id: str, amount_cents: Optional[int] = None) -> PaymentResult:
        """Capture an existing authorized transaction."""
        txn = self.recorded_transactions.get(transaction_id)
        if not txn:
            return PaymentResult(
                status=PaymentStatus.ERROR,
                success=False,
                error_message=f"Transaction '{transaction_id}' not found."
            )
        return PaymentResult(
            status=PaymentStatus.AUTHORIZED,
            success=True,
            transaction_id=transaction_id,
            timestamp=get_now_in_iso(),
            raw_details={"action": "captured", "amount_cents": amount_cents}
        )

    def refund(self, transaction_id: str, amount_cents: Optional[int] = None) -> PaymentResult:
        """
        Refund an authorized or captured transaction (return of funds).

        Simulation triggers via card numbers:
        - Card ending in '8888': Declined (Card issuer rejected return of funds / account closed)
        - Card ending in '7777': Error (Gateway timeout communicating with payment network)
        - Any other card: Successfully refunded

        Additional triggers:
        - Global mode ALWAYS_DECLINE or ALWAYS_ERROR
        - Transaction ID containing 'fail' or 'err'
        - Unknown transaction ID
        """
        # 1. Check global test modes
        if self.mode == StubMode.ALWAYS_DECLINE:
            return PaymentResult(
                status=PaymentStatus.DECLINED,
                success=False,
                error_message="Refund declined: Issuer declined return of funds.",
                timestamp=get_now_in_iso()
            )

        if self.mode == StubMode.ALWAYS_ERROR:
            return PaymentResult(
                status=PaymentStatus.ERROR,
                success=False,
                error_message="Refund failed: Gateway communication error."
            )

        # 2. Check transaction ID rules
        if "fail" in str(transaction_id).lower() or "err" in str(transaction_id).lower():
            return PaymentResult(
                status=PaymentStatus.DECLINED,
                success=False,
                error_message="Refund failed: Issuer rejected return of funds.",
                timestamp=get_now_in_iso()
            )

        txn = self.recorded_transactions.get(transaction_id)
        if not txn:
            return PaymentResult(
                status=PaymentStatus.ERROR,
                success=False,
                error_message=f"Transaction '{transaction_id}' not found."
            )

        # 3. Key CC numbers: evaluate original card ending digits
        card_last4 = txn.raw_details.get("last4")
        if card_last4 == "8888":
            return PaymentResult(
                status=PaymentStatus.DECLINED,
                success=False,
                error_message="Refund rejected by card issuer: Account closed or card invalid.",
                timestamp=get_now_in_iso(),
                raw_details={"refunded_transaction_id": transaction_id, "last4": card_last4}
            )
        elif card_last4 == "7777":
            return PaymentResult(
                status=PaymentStatus.ERROR,
                success=False,
                error_message="Refund failed: Gateway network timeout during return of funds.",
                raw_details={"refunded_transaction_id": transaction_id, "last4": card_last4}
            )

        return PaymentResult(
            status=PaymentStatus.AUTHORIZED,
            success=True,
            transaction_id=f"ref_{uuid.uuid4().hex[:12]}",
            timestamp=get_now_in_iso(),
            raw_details={"refunded_transaction_id": transaction_id, "amount_cents": amount_cents}
        )
