from datetime import datetime, timezone
from flask import Blueprint, request, jsonify
from database import db_session
from state_machine import OrderState, transition_order, InvalidStateTransitionError, TransitionGuardError, OrderNotFoundError
from .routes_common import _serialize_order, _extract_order_updates, maybe_transition_to_initialized, _parse_seats

order_bp = Blueprint("order", __name__)

@order_bp.route("/orders/<order_id>", methods=["PATCH", "PUT", "POST"])
def update_order(order_id):
    """
    General update endpoint for an existing order (user_id, contact info, and/or payment info).
    """
    data = request.get_json(silent=True) or {}
    updates = _extract_order_updates(data)

    if not updates:
        return jsonify({"error": "No update fields provided."}), 400

    with db_session() as conn:
        row = conn.execute("SELECT * FROM v_orders_latest_status WHERE id = ?", (order_id,)).fetchone()
        if not row:
            return jsonify({"error": f"Order #{order_id} not found."}), 404

        current_status = row["status"]
        if current_status in (OrderState.COMPLETE.value, OrderState.CANCELLED.value, OrderState.EXPIRED.value, OrderState.CLOSED.value):
            return jsonify({
                "error": f"Order #{order_id} is in terminal status '{current_status}' and cannot be modified."
            }), 400

        set_clauses = [f"{col} = ?" for col in updates.keys()]
        set_clauses.append("updated_at = CURRENT_TIMESTAMP")
        values = list(updates.values()) + [order_id]

        conn.execute(f"""
            UPDATE orders
            SET {", ".join(set_clauses)}
            WHERE id = ?
        """, tuple(values))

        updated_row = conn.execute("SELECT * FROM v_orders_latest_status WHERE id = ?", (order_id,)).fetchone()
        updated_row = maybe_transition_to_initialized(conn, updated_row, order_id)
        serialized_order = _serialize_order(updated_row, conn=conn)

    return jsonify({
        "message": f"Order #{order_id} updated successfully.",
        "order": serialized_order
    }), 200

@order_bp.route("/cancel_order", methods=["POST"])
def cancel_order():
    """
    Cancel an existing order.
    Validation and transition rules are authoritatively enforced by transition_order.
    """
    data = request.get_json(silent=True) or {}
    order_id = data.get("order_id")

    if not order_id:
        return jsonify({"error": "'order_id' is required."}), 400

    reason = data.get("reason", "Order cancelled")

    with db_session() as conn:
        row = conn.execute("SELECT * FROM v_orders_latest_status WHERE id = ?", (order_id,)).fetchone()
        if not row:
            return jsonify({"error": f"Order #{order_id} not found."}), 404

        try:
            updated_row = transition_order(conn, order_id, OrderState.CANCELLED, reason=reason)
        except (InvalidStateTransitionError, TransitionGuardError) as err:
            return jsonify({"error": str(err)}), 400

        serialized_order = _serialize_order(updated_row, conn=conn)

    return jsonify({
        "message": f"Order #{order_id} successfully cancelled.",
        "order": serialized_order
    }), 200

@order_bp.route("/orders/<order_id>/close", methods=["POST"])
def close_order(order_id):
    """
    Close an order (transition to terminal 'closed' status).
    Permitted for orders in 'cancelled' or 'refunded'.
    """
    data = request.get_json(silent=True) or {}
    reason = data.get("reason", "Order administratively closed")

    with db_session() as conn:
        row = conn.execute("SELECT * FROM v_orders_latest_status WHERE id = ?", (order_id,)).fetchone()
        if not row:
            return jsonify({"error": f"Order #{order_id} not found."}), 404

        current_status = row["status"]
        try:
            updated_row = transition_order(conn, order_id, OrderState.CLOSED, reason=reason)
        except (InvalidStateTransitionError, TransitionGuardError) as err:
            return jsonify({"error": str(err)}), 400

        serialized_order = _serialize_order(updated_row, conn=conn)

    return jsonify({
        "message": f"Order #{order_id} transitioned from '{current_status}' to 'closed'.",
        "order": serialized_order
    }), 200

@order_bp.route("/orders/<order_id>/resolve", methods=["POST"])
def resolve_order(order_id):
    """
    Resolve an order currently in 'needs_human_resolution'.
    Valid pathways from NEEDS_HUMAN_RESOLUTION:
    - 'complete': staff fulfills/re-delivers order
    - 'cancelled': staff aborts order (can subsequently step to refunded or closed)
    """
    data = request.get_json(silent=True) or {}
    target_state = data.get("target_state", "complete")
    reason = data.get("reason", "Human resolution applied")

    with db_session() as conn:
        row = conn.execute("SELECT * FROM v_orders_latest_status WHERE id = ?", (order_id,)).fetchone()
        if not row:
            return jsonify({"error": f"Order #{order_id} not found."}), 404

        current_status = row["status"]
        if current_status != OrderState.NEEDS_HUMAN_RESOLUTION.value:
            return jsonify({
                "error": f"Order #{order_id} is in status '{current_status}', not 'needs_human_resolution'."
            }), 400

        try:
            target_order_state = OrderState.from_str(target_state)
        except ValueError:
            return jsonify({"error": f"Invalid target state '{target_state}'."}), 400

        if target_order_state not in (OrderState.COMPLETE, OrderState.CANCELLED):
            return jsonify({
                "error": f"Cannot resolve order to '{target_state}'. Allowed targets: 'complete', 'cancelled'."
            }), 400

        updates = {}
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        try:
            if target_order_state == OrderState.COMPLETE:
                updates["delivered_at"] = now_str
                updates["processed_at"] = now_str
                seats_list = _parse_seats(row["seats"])
                existing_tickets = conn.execute("SELECT id FROM tickets WHERE order_id = ?", (order_id,)).fetchall()
                if not existing_tickets:
                    from .ticket_routes import _create_tickets_for_order
                    _create_tickets_for_order(conn, order_id, seats_list, created_at=now_str)
                updated_row = transition_order(conn, order_id, OrderState.COMPLETE, order_updates=updates, reason=reason)
            elif target_order_state == OrderState.CANCELLED:
                updated_row = transition_order(conn, order_id, OrderState.CANCELLED, reason=reason)
        except (InvalidStateTransitionError, TransitionGuardError) as err:
            return jsonify({"error": str(err)}), 400

        serialized_order = _serialize_order(updated_row, conn=conn)

    return jsonify({
        "message": f"Order #{order_id} successfully resolved and transitioned to '{updated_row['status']}'.",
        "order": serialized_order
    }), 200

@order_bp.route("/orders", methods=["GET"])
def list_orders():
    """List all orders in the system with their status history and order details."""
    with db_session() as conn:
        rows = conn.execute("SELECT * FROM v_orders_latest_status ORDER BY created_at DESC").fetchall()
        all_statuses = conn.execute("""
            SELECT order_id, status, created_at
            FROM order_status
            ORDER BY order_id, id ASC
        """).fetchall()

        history_map = {}
        for s in all_statuses:
            history_map.setdefault(s["order_id"], []).append({
                "status": s["status"],
                "created_at": str(s["created_at"])
            })

        orders = []
        for r in rows:
            order_data = _serialize_order(r)
            order_data["status_history"] = history_map.get(r["id"], [])
            orders.append(order_data)

    return jsonify({"orders": orders, "count": len(orders)}), 200

@order_bp.route("/orders/<order_id>", methods=["GET"])
def get_order(order_id):
    """Retrieve details for a specific order."""
    with db_session() as conn:
        row = conn.execute("SELECT * FROM v_orders_latest_status WHERE id = ?", (order_id,)).fetchone()
        if not row:
            return jsonify({"error": f"Order #{order_id} not found."}), 404
        return jsonify({"order": _serialize_order(row, conn=conn)}), 200
