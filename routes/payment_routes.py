import time
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from config import Config
from database import db_session
from state_machine import OrderState, transition_order, InvalidStateTransitionError, TransitionGuardError
from payment_gateway import get_payment_gateway, PaymentRequest
from .routes_common import _serialize_order, _get_missing_order_fields, _parse_seats

payment_bp = Blueprint("payment", __name__)

@payment_bp.route("/orders/<order_id>/process_payment", methods=["POST"])
def process_payment(order_id):
    """
    Process payment for an order through the payment gateway interface.
    Validates order completeness and transitions order to 'payment_authorized' on success,
    or 'payment_failed' on card decline.
    """
    with db_session() as conn:
        row = conn.execute("SELECT * FROM v_orders_latest_status WHERE id = ?", (order_id,)).fetchone()
        if not row:
            return jsonify({"error": f"Order #{order_id} not found."}), 404

        current_status = row["status"]
        if current_status not in (OrderState.INITIALIZED.value, OrderState.HELD.value, OrderState.PAYMENT_FAILED.value):
            return jsonify({
                "error": f"Cannot process payment for order in status '{current_status}'."
            }), 400

        # Check if the seat hold has expired
        if current_status == OrderState.HELD.value:
            now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            hold_expires = row["hold_expires_at"]
            if hold_expires and str(hold_expires) <= now_str:
                expired_row = transition_order(conn, order_id, OrderState.EXPIRED)
                return jsonify({
                    "error": f"Seat hold for Order #{order_id} has expired (hold limit was {Config.SEAT_HOLD_TTL} minutes).",
                    "order": _serialize_order(expired_row, conn=conn)
                }), 400

        # Validate order completeness
        missing_fields = _get_missing_order_fields(row)
        if missing_fields:
            return jsonify({
                "error": "Cannot process payment: order details are incomplete.",
                "missing_fields": missing_fields,
                "order": _serialize_order(row, conn=conn)
            }), 400

        try:
            # Advance order to 'initialized' before charging
            if current_status == OrderState.HELD.value:
                row = transition_order(conn, order_id, OrderState.INITIALIZED)
            elif current_status == OrderState.PAYMENT_FAILED.value:
                transition_order(conn, order_id, OrderState.HELD)
                row = transition_order(conn, order_id, OrderState.INITIALIZED)

            seats_list = _parse_seats(row["seats"])
            num_seats = len(seats_list) or 1
            amount_cents = num_seats * Config.TICKET_PRICE_CENTS

            if Config.SIMULATE_WORK_DELAY_SECONDS > 0:
                time.sleep(Config.SIMULATE_WORK_DELAY_SECONDS)

            gateway = get_payment_gateway()
            pay_req = PaymentRequest(
                order_id=order_id,
                amount_cents=amount_cents,
                cc_name=row["cc_name"] or "",
                cc_number=row["cc_number"] or "",
                cc_expiry=row["cc_expiry"] or "",
                cc_last4=row["cc_last4"] or "",
                metadata={"user_id": row["user_id"], "seats": seats_list}
            )
            pay_res = gateway.authorize(pay_req)

            if not pay_res.success:
                failed_row = transition_order(conn, order_id, OrderState.PAYMENT_FAILED)
                return jsonify({
                    "error": pay_res.error_message or "Payment declined.",
                    "payment": pay_res.to_dict(),
                    "order": _serialize_order(failed_row, conn=conn)
                }), 402

            success_row = transition_order(
                conn,
                order_id,
                OrderState.PAYMENT_AUTHORIZED,
                order_updates={"payment_transaction_id": pay_res.transaction_id}
            )
            serialized = _serialize_order(success_row, conn=conn)

        except (InvalidStateTransitionError, TransitionGuardError) as err:
            return jsonify({"error": str(err)}), 400

    return jsonify({
        "message": f"Payment successfully authorized for Order #{order_id}.",
        "payment": pay_res.to_dict(),
        "order": serialized
    }), 200

@payment_bp.route("/orders/<order_id>/refund", methods=["POST"])
def refund_order(order_id):
    """
    Refund an order that has completed payment.
    Eligible statuses: 'complete', 'needs_human_resolution'

    Request body (optional):
    - amount_cents: (int, optional) Amount in cents to refund. Defaults to total ticket price.
    - reason: (string, optional) Reason for refund.

    Behavior:
    - Both 'complete' and 'needs_human_resolution' advance to 'cancelled' first.
    - Gateway refund is executed:
      - If refund succeeds: order transitions cancelled -> refunded -> closed (terminal).
      - If refund fails: order transitions cancelled -> refund_failed -> needs_human_resolution.
    - Any issued tickets are voided.
    """
    data = request.get_json(silent=True) or {}

    with db_session() as conn:
        row = conn.execute("SELECT * FROM v_orders_latest_status WHERE id = ?", (order_id,)).fetchone()
        if not row:
            return jsonify({"error": f"Order #{order_id} not found."}), 404

        current_status = row["status"]
        eligible_statuses = (
            OrderState.COMPLETE.value,
            OrderState.CANCELLED.value,
            OrderState.NEEDS_HUMAN_RESOLUTION.value,
        )
        if current_status not in eligible_statuses:
            return jsonify({
                "error": f"Cannot refund order in '{current_status}' status. "
                         f"Only orders in {list(eligible_statuses)} can be refunded."
            }), 400

        txn_id = row["payment_transaction_id"]
        if not txn_id:
            return jsonify({
                "error": f"Order #{order_id} does not have a payment transaction ID to refund."
            }), 400

        # Determine refund amount
        amount_cents = data.get("amount_cents")
        if not amount_cents:
            seats_list = _parse_seats(row["seats"])
            num_seats = len(seats_list) or 1
            amount_cents = num_seats * Config.TICKET_PRICE_CENTS

        reason = data.get("reason", "Customer requested cancellation and refund")

        # Both 'complete' and 'needs_human_resolution' advance to 'cancelled' first
        if current_status in (OrderState.COMPLETE.value, OrderState.NEEDS_HUMAN_RESOLUTION.value):
            transition_order(conn, order_id, OrderState.CANCELLED, reason=reason)

        gateway = get_payment_gateway()
        refund_res = gateway.refund(transaction_id=txn_id, amount_cents=amount_cents)

        if not refund_res.success:
            # Return of funds failed -> refund_failed -> needs_human_resolution
            transition_order(conn, order_id, OrderState.REFUND_FAILED, reason=refund_res.error_message)
            updated_row = transition_order(conn, order_id, OrderState.NEEDS_HUMAN_RESOLUTION, reason=refund_res.error_message)

            conn.execute("UPDATE tickets SET status = 'void' WHERE order_id = ?", (order_id,))
            return jsonify({
                "error": refund_res.error_message or "Refund failed.",
                "refund": refund_res.to_dict(),
                "order": _serialize_order(updated_row, conn=conn)
            }), 402

        # Refund succeeded:
        # 1. Transition to 'refunded' (shows in audit history as state order passed through)
        transition_order(conn, order_id, OrderState.REFUNDED, reason=reason)

        # 2. Transition to terminal status 'closed'
        updated_row = transition_order(conn, order_id, OrderState.CLOSED, reason=reason)

        # 3. Mark any tickets as 'void'
        conn.execute("UPDATE tickets SET status = 'void' WHERE order_id = ?", (order_id,))

        serialized = _serialize_order(updated_row, conn=conn)

    return jsonify({
        "message": f"Refund of ${amount_cents / 100:.2f} successfully processed for Order #{order_id}. Order is now closed.",
        "refund": refund_res.to_dict(),
        "order": serialized
    }), 200
