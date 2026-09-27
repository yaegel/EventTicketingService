"""
Payment gateway abstraction and interface definitions.

Provides:
- PaymentStatus: String enum for payment result statuses.
- PaymentRequest: Structured request data object for authorizing payments.
- PaymentResult: Structured outcome data object for payment operations.
- PaymentGateway: Abstract base class defining the payment gateway interface.
- get_payment_gateway / set_payment_gateway / reset_payment_gateway: Gateway accessors.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Dict, Any

class PaymentStatus(str, Enum):
    AUTHORIZED = "authorized"
    DECLINED = "declined"
    ERROR = "error"

@dataclass
class PaymentRequest:
    order_id: Any
    amount_cents: int
    cc_name: str
    cc_number: str
    cc_expiry: str
    cc_last4: str
    currency: str = "USD"
    metadata: Dict[str, Any] = field(default_factory=dict)

@dataclass
class PaymentResult:
    status: PaymentStatus
    success: bool
    transaction_id: Optional[str] = None
    authorization_code: Optional[str] = None
    error_message: Optional[str] = None
    timestamp: Optional[str] = None
    raw_details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize result to a clean dictionary."""
        return {
            "status": self.status.value,
            "success": self.success,
            "transaction_id": self.transaction_id,
            "authorization_code": self.authorization_code,
            "error_message": self.error_message,
            "timestamp": self.timestamp,
        }

class PaymentGateway(ABC):
    """
    Abstract base class defining the payment gateway interface.
    Production implementations connect to real processors (e.g. Stripe, Adyen, Braintree).
    """

    @abstractmethod
    def authorize(self, request: PaymentRequest) -> PaymentResult:
        """
        Authorize a payment transaction for an order.
        Validates card details and places a hold on funds without capturing immediately.
        """
        pass

    @abstractmethod
    def capture(self, transaction_id: str, amount_cents: Optional[int] = None) -> PaymentResult:
        """Capture a previously authorized transaction."""
        pass

    @abstractmethod
    def refund(self, transaction_id: str, amount_cents: Optional[int] = None) -> PaymentResult:
        """Refund a captured transaction."""
        pass


# Active payment gateway instance (defaults to StubPaymentGateway)
_current_gateway: Optional[PaymentGateway] = None

def get_payment_gateway() -> PaymentGateway:
    """Retrieve the current active payment gateway instance."""
    global _current_gateway
    if _current_gateway is None:
        from stub_payment_gateway import StubPaymentGateway
        _current_gateway = StubPaymentGateway()
    return _current_gateway

def set_payment_gateway(gateway: PaymentGateway) -> None:
    """Set the active payment gateway (useful for swapping implementations in tests)."""
    global _current_gateway
    _current_gateway = gateway

def reset_payment_gateway() -> None:
    """Reset to a fresh StubPaymentGateway instance."""
    global _current_gateway
    from stub_payment_gateway import StubPaymentGateway
    _current_gateway = StubPaymentGateway()
