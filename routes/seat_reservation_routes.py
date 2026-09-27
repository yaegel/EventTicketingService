import json
from datetime import datetime, timezone, timedelta
from flask import Blueprint, request, jsonify
from config import Config
from database import db_session
from state_machine import OrderState, transition_order, initiate_order_status
from helpers.uuid_helpers import generate_uuid7
from .routes_common import _serialize_order, _parse_seats

seat_reservation_bp = Blueprint("seat_reservation", __name__)

@seat_reservation_bp.route("/claim_seats", methods=["POST"])
def claim_seats():
    """
    Claim seats for a customer (or guest user) and reserve them with a temporary hold.
    Initial state: 'held'
    Seats are held for Config.SEAT_HOLD_TTL minutes.
    """
    data = request.get_json(silent=True) or {}
    user_id = data.get("user_id")
    seats = data.get("seats")

    if not user_id or not seats:
        return jsonify({"error": "'user_id' and 'seats' inputs are required to hold seats."}), 400

    # Normalize seats to list
    seats_list = seats if isinstance(seats, list) else [seats]
    seats_str = json.dumps(seats_list)

    now = datetime.now(timezone.utc)
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")

    with db_session() as conn:
        # 1. Proactively mark any holds that have expired past their TTL
        stale_holds = conn.execute("""
            SELECT id
            FROM v_orders_latest_status
            WHERE status = 'held'
              AND hold_expires_at IS NOT NULL
              AND hold_expires_at <= ?
        """, (now_str,)).fetchall()

        for hold in stale_holds:
            transition_order(conn, hold["id"], OrderState.EXPIRED)

        # 2. Check for conflicts with active (unexpired, non-cancelled) orders
        active_orders = conn.execute("""
            SELECT id, seats, status
            FROM v_orders_latest_status
            WHERE status IN ('held', 'initialized', 'payment_authorized', 'payment_failed', 'ticket_delivery_failed', 'complete')
               OR (status = 'needs_human_resolution' AND id IN (SELECT order_id FROM tickets WHERE status != 'void'))
        """).fetchall()

        requested_seats_set = set(str(s) for s in seats_list)
        conflicting_seats = set()

        for order in active_orders:
            order_seats = _parse_seats(order["seats"])
            conflicts = requested_seats_set.intersection(set(order_seats))
            if conflicts:
                conflicting_seats.update(conflicts)

        if conflicting_seats:
            return jsonify({
                "error": f"Seat(s) {sorted(list(conflicting_seats))} are currently held or already booked.",
                "conflicting_seats": sorted(list(conflicting_seats))
            }), 409

        # 3. Calculate hold expiration timestamp
        expires_at = now + timedelta(minutes=Config.SEAT_HOLD_TTL)
        expires_at_str = expires_at.strftime("%Y-%m-%d %H:%M:%S")

        # 4. Generate UUIDv7 order ID and insert new hold order into database
        order_id = generate_uuid7()
        conn.execute("""
            INSERT INTO orders (id, user_id, seats, held_at, hold_expires_at)
            VALUES (?, ?, ?, ?, ?)
        """, (order_id, user_id, seats_str, now_str, expires_at_str))

        # 5. Record initial status via state machine engine
        row = initiate_order_status(conn, order_id, OrderState.HELD)
        serialized_order = _serialize_order(row, conn=conn)

    return jsonify({
        "message": f"Seats held successfully for {Config.SEAT_HOLD_TTL} minutes.",
        "order": serialized_order
    }), 201
