"""
Authoritative state machine engine for Order lifecycle management.
Serves as the exclusive single source of truth for:
- Order states
- State transition graphs
- Domain guard conditions (invariants)
- Persisting state transitions to the database
"""
import sqlite3
from enum import Enum
from typing import Set, Dict, Optional, Any, List
from helpers.date_helpers import get_now_utc_str

class OrderState(str, Enum):
    HELD = "held"
    INITIALIZED = "initialized"
    PAYMENT_AUTHORIZED = "payment_authorized"
    PAYMENT_FAILED = "payment_failed"
    TICKET_DELIVERY_FAILED = "ticket_delivery_failed"
    COMPLETE = "complete"
    CANCELLED = "cancelled"
    REFUND_FAILED = "refund_failed"
    NEEDS_HUMAN_RESOLUTION = "needs_human_resolution"
    REFUNDED = "refunded"
    CLOSED = "closed"
    EXPIRED = "expired"

    @classmethod
    def from_str(cls, state_name: Any) -> "OrderState":
        """Convert an input string or OrderState instance into its matching OrderState enum member."""
        if isinstance(state_name, cls):
            return state_name
        if hasattr(state_name, "value"):
            state_name = state_name.value
        if not state_name:
            raise ValueError("State name cannot be empty.")
        val = str(state_name).strip().lower()
        if val.startswith("orderstate."):
            val = val.split(".", 1)[1]
        return cls(val)

class OrderNotFoundError(Exception):
    """Raised when an order does not exist in the database."""
    def __init__(self, order_id: Any):
        super().__init__(f"Order #{order_id} not found.")
        self.order_id = order_id

class InvalidStateTransitionError(Exception):
    """Raised when an invalid state transition graph edge is attempted."""
    def __init__(self, current_state: str, target_state: str):
        super().__init__(
            f"Invalid state transition: Cannot transition order from '{current_state}' to '{target_state}'."
        )
        self.current_state = current_state
        self.target_state = target_state

class TransitionGuardError(Exception):
    """Raised when transition prerequisites or guard invariants are not met."""
    def __init__(self, current_state: str, target_state: str, reason: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(
            f"Cannot transition order from '{current_state}' to '{target_state}': {reason}"
        )
        self.current_state = current_state
        self.target_state = target_state
        self.reason = reason
        self.details = details or {}

VALID_TRANSITIONS: Dict[OrderState, Set[OrderState]] = {
    OrderState.HELD: {
        OrderState.INITIALIZED,
        OrderState.CANCELLED,
        OrderState.EXPIRED,
    },
    OrderState.INITIALIZED: {
        OrderState.PAYMENT_AUTHORIZED,
        OrderState.PAYMENT_FAILED,
    },
    OrderState.PAYMENT_AUTHORIZED: {
        OrderState.TICKET_DELIVERY_FAILED,
        OrderState.COMPLETE,
    },
    OrderState.PAYMENT_FAILED: {
        OrderState.HELD,
        OrderState.CANCELLED,
    },
    OrderState.COMPLETE: {
        OrderState.CANCELLED,
    },
    OrderState.CANCELLED: {
        OrderState.REFUNDED,
        OrderState.REFUND_FAILED,
        OrderState.CLOSED
    },
    OrderState.REFUND_FAILED: {
        OrderState.NEEDS_HUMAN_RESOLUTION,
    },
    OrderState.TICKET_DELIVERY_FAILED: {
        OrderState.NEEDS_HUMAN_RESOLUTION,
    },
    OrderState.NEEDS_HUMAN_RESOLUTION: {
        OrderState.COMPLETE,
        OrderState.CANCELLED,
    },
    OrderState.REFUNDED: {
        OrderState.CLOSED,
    },
    OrderState.CLOSED: set(),      # Terminal state for cancelled & refunded orders
    OrderState.EXPIRED: set(),     # Terminal state for abandoned seat holds
}

def can_transition(current_state: str, target_state: str) -> bool:
    """Return True if transition from current_state to target_state is permitted."""
    try:
        source = OrderState.from_str(current_state)
        target = OrderState.from_str(target_state)
        return target in VALID_TRANSITIONS.get(source, set())
    except ValueError:
        return False

def validate_transition(current_state: str, target_state: str) -> None:
    """Validate that the state transition is allowed; raise InvalidStateTransitionError if not."""
    if not can_transition(current_state, target_state):
        raise InvalidStateTransitionError(current_state, target_state)

# --- Transition Guards (Domain Invariants) ---

REQUIRED_FIELDS_FOR_INITIALIZED: List[tuple] = [
    ("user_id", "User ID (user_id)"),
    ("fname", "First Name (fname)"),
    ("lname", "Last Name (lname)"),
    ("email", "Email Address (email)"),
    ("phone", "Phone Number (phone)"),
    ("addr_street", "Street Address (addr_street)"),
    ("addr_city", "City (addr_city)"),
    ("addr_state", "State (addr_state)"),
    ("addr_zip", "ZIP Code (addr_zip)"),
    ("cc_name", "Cardholder Name (cc_name)"),
    ("cc_number", "Credit Card Number (cc_number)"),
    ("cc_expiry", "Card Expiration Date (cc_expiry)"),
]

def _guard_held_not_expired(
    current_state: OrderState,
    target_state: OrderState,
    row: sqlite3.Row,
    order_updates: Dict[str, Any],
    reason: Optional[str]
) -> None:
    """Ensure a seat hold has not expired before transitioning from HELD (unless expiring)."""
    if current_state == OrderState.HELD and target_state != OrderState.EXPIRED:
        hold_expires = row["hold_expires_at"]
        now_str = get_now_utc_str()
        if hold_expires and str(hold_expires) <= now_str:
            raise TransitionGuardError(
                current_state.value,
                target_state.value,
                "Seat hold has expired."
            )

def _guard_order_complete_for_initialized(
    current_state: OrderState,
    target_state: OrderState,
    row: sqlite3.Row,
    order_updates: Dict[str, Any],
    reason: Optional[str]
) -> None:
    """Ensure contact information and card details are complete before INITIALIZED."""
    if target_state == OrderState.INITIALIZED:
        row_keys = row.keys() if hasattr(row, "keys") else []
        missing = []
        for field, label in REQUIRED_FIELDS_FOR_INITIALIZED:
            # Check updates first, then row
            val = order_updates.get(field) if field in order_updates else (row[field] if field in row_keys else None)
            if not val or not str(val).strip():
                missing.append(label)
        if missing:
            raise TransitionGuardError(
                current_state.value,
                target_state.value,
                "Order details are incomplete. Contact information and payment cc information are required.",
                details={"missing_fields": missing}
            )

def _guard_payment_authorized_has_transaction(
    current_state: OrderState,
    target_state: OrderState,
    row: sqlite3.Row,
    order_updates: Dict[str, Any],
    reason: Optional[str]
) -> None:
    """Ensure payment_transaction_id exists when transitioning to PAYMENT_AUTHORIZED."""
    if target_state == OrderState.PAYMENT_AUTHORIZED:
        txn_id = order_updates.get("payment_transaction_id") or row["payment_transaction_id"]
        if not txn_id:
            raise TransitionGuardError(
                current_state.value,
                target_state.value,
                "Cannot authorize order without payment transaction ID."
            )

def _guard_refund_requires_payment(
    current_state: OrderState,
    target_state: OrderState,
    row: sqlite3.Row,
    order_updates: Dict[str, Any],
    reason: Optional[str]
) -> None:
    """Ensure an order has an authorized payment transaction before transitioning to REFUNDED or REFUND_FAILED."""
    if target_state in (OrderState.REFUNDED, OrderState.REFUND_FAILED):
        txn_id = order_updates.get("payment_transaction_id") or row["payment_transaction_id"]
        if not txn_id:
            raise TransitionGuardError(
                current_state.value,
                target_state.value,
                f"Cannot refund order #{row['id']} because no payment transaction ID exists."
            )

_GUARDS = [
    _guard_held_not_expired,
    _guard_order_complete_for_initialized,
    _guard_payment_authorized_has_transaction,
    _guard_refund_requires_payment,
]

# --- Authoritative Database Transition Engine ---

def initiate_order_status(
    conn: sqlite3.Connection,
    order_id: str | int,
    initial_state: OrderState | str = OrderState.HELD
) -> sqlite3.Row:
    """
    Record the initial status for a newly created order.
    The ONLY valid way to create the initial order_status record.
    """
    state_enum = OrderState.from_str(initial_state)
    existing = conn.execute("SELECT id FROM order_status WHERE order_id = ? LIMIT 1", (order_id,)).fetchone()
    if existing:
        raise ValueError(f"Order #{order_id} already has a recorded status history.")

    now_str = get_now_utc_str()
    conn.execute("""
        INSERT INTO order_status (order_id, status, created_at)
        VALUES (?, ?, ?)
    """, (order_id, state_enum.value, now_str))

    conn.execute("UPDATE orders SET updated_at = ? WHERE id = ?", (now_str, order_id))
    return conn.execute("SELECT * FROM v_orders_latest_status WHERE id = ?", (order_id,)).fetchone()

def transition_order(
    conn: sqlite3.Connection,
    order_id: str | int,
    target_state: OrderState | str,
    *,
    order_updates: Optional[Dict[str, Any]] = None,
    reason: Optional[str] = None
) -> sqlite3.Row:
    """
    The authoritative chokepoint to transition an order's status.
    
    1. Fetches current order state from DB.
    2. Validates against VALID_TRANSITIONS graph.
    3. Runs domain guards.
    4. Atomically applies any order_updates.
    5. Inserts into order_status and updates updated_at.
    6. Returns updated order row from v_orders_latest_status.
    """
    target_enum = OrderState.from_str(target_state)

    row = conn.execute("SELECT * FROM v_orders_latest_status WHERE id = ?", (order_id,)).fetchone()
    if not row:
        raise OrderNotFoundError(order_id)

    current_enum = OrderState.from_str(row["status"])

    # 1. State machine graph validation
    validate_transition(current_enum, target_enum)

    # 2. Run domain guards
    updates_dict = dict(order_updates or {})
    for guard in _GUARDS:
        guard(current_enum, target_enum, row, updates_dict, reason)

    # 3. Apply atomic order table updates
    now_str = get_now_utc_str()
    if target_enum == OrderState.EXPIRED and "expired_at" not in updates_dict:
        updates_dict["expired_at"] = now_str

    if updates_dict:
        set_clauses = [f"{col} = ?" for col in updates_dict.keys()]
        set_clauses.append("updated_at = ?")
        values = list(updates_dict.values()) + [now_str, order_id]
        conn.execute(f"UPDATE orders SET {', '.join(set_clauses)} WHERE id = ?", tuple(values))
    else:
        conn.execute("UPDATE orders SET updated_at = ? WHERE id = ?", (now_str, order_id))

    # 4. Insert status history record
    conn.execute("""
        INSERT INTO order_status (order_id, status, created_at)
        VALUES (?, ?, ?)
    """, (order_id, target_enum.value, now_str))

    # 5. Return updated row
    updated_row = conn.execute("SELECT * FROM v_orders_latest_status WHERE id = ?", (order_id,)).fetchone()
    return updated_row
