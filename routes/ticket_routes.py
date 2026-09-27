import time
import uuid
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from config import Config
from database import db_session
from state_machine import OrderState, transition_order
from .routes_common import _serialize_order, _parse_seats

ticket_bp = Blueprint("ticket", __name__)

def _serialize_tickets(ticket_rows):
    """Format ticket DB rows into a clean list of dictionaries."""
    return [
        {
            "id": t["id"],
            "seat": t["seat"],
            "ticket_code": t["ticket_code"],
            "status": t["status"],
            "created_at": str(t["created_at"])
        }
        for t in ticket_rows
    ]

def _fetch_tickets_for_order(conn, order_id):
    """Fetch all ticket rows for an order sorted by ID."""
    return conn.execute("""
        SELECT id, seat, ticket_code, status, created_at
        FROM tickets
        WHERE order_id = ?
        ORDER BY id ASC
    """, (order_id,)).fetchall()

def _create_tickets_for_order(conn, order_id, seats_list, created_at=None):
    """Insert ticket records for the given seats into tickets table and return formatted dicts."""
    now_str = created_at or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    created = []
    for seat in seats_list:
        code = f"TKT-{order_id}-{seat}-{uuid.uuid4().hex[:6].upper()}"
        cursor = conn.execute("""
            INSERT INTO tickets (order_id, seat, ticket_code, status, created_at)
            VALUES (?, ?, ?, 'issued', ?)
        """, (order_id, str(seat), code, now_str))
        created.append({
            "id": cursor.lastrowid,
            "seat": str(seat),
            "ticket_code": code,
            "status": "issued",
            "created_at": now_str
        })
    return created

def _ensure_tickets_for_order(conn, row, order_id):
    """
    Ensure ticket records exist for the given order; auto-generates them if missing.
    Simulates ticket PDF / QR-barcode rendering work if new tickets are generated.
    Returns the list of ticket rows.
    """
    ticket_rows = _fetch_tickets_for_order(conn, order_id)
    if not ticket_rows:
        if Config.SIMULATE_WORK_DELAY_SECONDS > 0:
            time.sleep(Config.SIMULATE_WORK_DELAY_SECONDS)
        seats_list = _parse_seats(row["seats"])
        _create_tickets_for_order(conn, order_id, seats_list)
        ticket_rows = _fetch_tickets_for_order(conn, order_id)
    return ticket_rows

@ticket_bp.route("/orders/<order_id>/deliver_tickets", methods=["POST"])
def deliver_tickets(order_id):
    """
    Deliver tickets to customer (e.g. email or SMS dispatch).
    Creates tickets first if they do not already exist, then dispatches them.
    - If delivery fails: transitions payment_authorized -> ticket_delivery_failed -> needs_human_resolution.
    - If delivery succeeds: transitions payment_authorized / needs_human_resolution -> complete.
    """
    data = request.get_json(silent=True) or {}
    delivery_channel = data.get("channel", "email")

    with db_session() as conn:
        row = conn.execute("SELECT * FROM v_orders_latest_status WHERE id = ?", (order_id,)).fetchone()
        if not row:
            return jsonify({"error": f"Order #{order_id} not found."}), 404

        current_status = row["status"]
        if current_status not in (OrderState.PAYMENT_AUTHORIZED.value, OrderState.COMPLETE.value, OrderState.NEEDS_HUMAN_RESOLUTION.value):
            return jsonify({
                "error": f"Cannot deliver tickets for order in status '{current_status}'. "
                         f"Order must be 'payment_authorized', 'needs_human_resolution', or 'complete'."
            }), 400

        # Ensure tickets exist for this order; auto-generate if missing
        ticket_rows = _ensure_tickets_for_order(conn, row, order_id)

        # Simulate delivery dispatch work (email/SMS gateway)
        if Config.SIMULATE_WORK_DELAY_SECONDS > 0:
            time.sleep(Config.SIMULATE_WORK_DELAY_SECONDS)

        # Check for delivery failure triggers
        recipient = row["email"] or row["user_id"] or ""
        fail_requested = bool(data.get("simulate_failure") or data.get("fail_delivery") or data.get("fail"))
        email_bounces = any(pattern in recipient.lower() for pattern in ("fail", "bounce", "undeliverable", "@invalid", ".invalid")) or ("@" not in recipient and not row["phone"])
        channel_invalid = delivery_channel not in ("email", "sms")

        if fail_requested or email_bounces or channel_invalid:
            failure_reason = data.get("error_message") or "Ticket delivery failed: Recipient address rejected or delivery dispatch error."
            if current_status == OrderState.PAYMENT_AUTHORIZED.value:
                transition_order(conn, order_id, OrderState.TICKET_DELIVERY_FAILED, reason=failure_reason)
                updated_row = transition_order(conn, order_id, OrderState.NEEDS_HUMAN_RESOLUTION, reason=failure_reason)
            else:
                updated_row = row

            return jsonify({
                "error": failure_reason,
                "order_id": order_id,
                "delivery_channel": delivery_channel,
                "recipient": recipient,
                "order": _serialize_order(updated_row, conn=conn)
            }), 502

        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        # If in payment_authorized or needs_human_resolution, transition order to complete
        if current_status in (OrderState.PAYMENT_AUTHORIZED.value, OrderState.NEEDS_HUMAN_RESOLUTION.value):
            updated_row = transition_order(
                conn,
                order_id,
                OrderState.COMPLETE,
                order_updates={"delivered_at": now_str}
            )
        else:
            conn.execute("""
                UPDATE orders
                SET delivered_at = ?,
                    updated_at = ?
                WHERE id = ?
            """, (now_str, now_str, order_id))
            updated_row = conn.execute("SELECT * FROM v_orders_latest_status WHERE id = ?", (order_id,)).fetchone()

        tickets_list = _serialize_tickets(ticket_rows)
        serialized = _serialize_order(updated_row, conn=conn)

    return jsonify({
        "message": f"Tickets for Order #{order_id} successfully delivered via {delivery_channel}.",
        "order_id": order_id,
        "delivery_channel": delivery_channel,
        "recipient": recipient,
        "delivered_at": now_str,
        "ticket_count": len(tickets_list),
        "order": serialized
    }), 200

@ticket_bp.route("/orders/<order_id>/tickets", methods=["GET"])
def get_order_tickets(order_id):
    """Retrieve all generated tickets for an order."""
    with db_session() as conn:
        row = conn.execute("SELECT id FROM orders WHERE id = ?", (order_id,)).fetchone()
        if not row:
            return jsonify({"error": f"Order #{order_id} not found."}), 404

        ticket_rows = _fetch_tickets_for_order(conn, order_id)
        tickets = _serialize_tickets(ticket_rows)

    return jsonify({
        "order_id": order_id,
        "tickets": tickets,
        "count": len(tickets)
    }), 200
