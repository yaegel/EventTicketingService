import json
from database import db_session
from state_machine import OrderState, transition_order, REQUIRED_FIELDS_FOR_INITIALIZED

def _parse_seats(seats_raw):
    """
    Safely parse seats from a JSON string, list, or scalar into a list of strings.
    Guarantees consistent seat list representation across all routes and services.
    """
    if isinstance(seats_raw, list):
        return [str(s) for s in seats_raw]
    if isinstance(seats_raw, str):
        try:
            parsed = json.loads(seats_raw)
            if isinstance(parsed, list):
                return [str(s) for s in parsed]
            return [str(parsed)]
        except (ValueError, TypeError):
            trimmed = seats_raw.strip()
            return [trimmed] if trimmed else []
    if seats_raw is not None:
        return [str(seats_raw)]
    return []

def _extract_order_updates(data):
    """
    Extract user_id, contact_info, and payment_info from request payload.
    Supports either nested dictionaries or flat parameters.
    """
    if not isinstance(data, dict):
        return {}

    updates = {}

    # 1. User ID (e.g. replacing guest user_id with logged in user_id)
    user_id = data.get("user_id")
    if user_id:
        updates["user_id"] = str(user_id).strip()

    # 2. Contact Information
    contact_data = data.get("contact_info") if isinstance(data.get("contact_info"), dict) else {}
    for field in ("fname", "lname", "email", "phone", "addr_street", "addr_street2", "addr_city", "addr_state", "addr_zip"):
        val = contact_data.get(field) or data.get(field)
        if val is not None:
            updates[field] = str(val).strip()

    # 3. Payment CC Information
    cc_data = data.get("payment_info") or data.get("cc_info")
    if not isinstance(cc_data, dict):
        cc_data = {}
    for field in ("cc_name", "cc_number", "cc_expiry"):
        val = cc_data.get(field) or data.get(field)
        if val is not None:
            updates[field] = str(val).strip()

    if "cc_number" in updates:
        raw_digits = "".join(filter(str.isdigit, updates["cc_number"]))
        updates["cc_last4"] = raw_digits[-4:] if len(raw_digits) >= 4 else raw_digits

    return updates

def _get_missing_order_fields(row):
    """
    Return list of required field labels that are missing for an order
    before it can transition to INITIALIZED.
    """
    row_keys = row.keys() if hasattr(row, "keys") else []
    missing = []
    for field, label in REQUIRED_FIELDS_FOR_INITIALIZED:
        val = row[field] if field in row_keys else None
        if not val or not str(val).strip():
            missing.append(label)
    return missing

def maybe_transition_to_initialized(conn, row, order_id):
    """
    If an order in 'held' or 'payment_failed' has all required contact and payment
    information fully filled out, progress the state along to 'initialized'.
    Returns the refreshed sqlite3.Row.
    """
    current_status = row["status"]
    if current_status not in (OrderState.HELD.value, OrderState.PAYMENT_FAILED.value):
        return row

    if not _get_missing_order_fields(row):
        try:
            if current_status == OrderState.PAYMENT_FAILED.value:
                transition_order(conn, order_id, OrderState.HELD)
            row = transition_order(conn, order_id, OrderState.INITIALIZED)
        except Exception:
            pass
    return row

def _fetch_order_status_history(conn, order_id):
    """Retrieve full audit history for an order."""
    try:
        status_rows = conn.execute("""
            SELECT status, created_at
            FROM order_status
            WHERE order_id = ?
            ORDER BY id ASC
        """, (order_id,)).fetchall()
        return [
            {"status": s["status"], "created_at": str(s["created_at"])}
            for s in status_rows
        ]
    except Exception:
        return []

def _fetch_order_tickets(conn, order_id):
    """Retrieve all tickets issued for an order."""
    try:
        ticket_rows = conn.execute("""
            SELECT id, seat, ticket_code, status, created_at
            FROM tickets
            WHERE order_id = ?
            ORDER BY id ASC
        """, (order_id,)).fetchall()
        if not ticket_rows:
            return None
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
    except Exception:
        return None

def _serialize_order(row, conn=None, status_history=None):
    """Format SQLite row into a clean dictionary response with full status history and order details."""
    if not row:
        return None
    seats = _parse_seats(row["seats"])
    row_keys = row.keys() if hasattr(row, "keys") else []
    order_id = row["id"]

    resolved_history = list(status_history) if status_history is not None else None
    tickets = None

    if conn is not None:
        if resolved_history is None:
            resolved_history = _fetch_order_status_history(conn, order_id)
        tickets = _fetch_order_tickets(conn, order_id)
    else:
        try:
            with db_session() as temp_conn:
                if resolved_history is None:
                    resolved_history = _fetch_order_status_history(temp_conn, order_id)
                tickets = _fetch_order_tickets(temp_conn, order_id)
        except Exception:
            if resolved_history is None:
                resolved_history = []

    user_id = row["user_id"] if "user_id" in row_keys and row["user_id"] else None

    contact_info = {
        "fname": row["fname"] if "fname" in row_keys and row["fname"] else None,
        "lname": row["lname"] if "lname" in row_keys and row["lname"] else None,
        "email": row["email"] if "email" in row_keys and row["email"] else None,
        "phone": row["phone"] if "phone" in row_keys and row["phone"] else None,
        "addr_street": row["addr_street"] if "addr_street" in row_keys and row["addr_street"] else None,
        "addr_street2": row["addr_street2"] if "addr_street2" in row_keys and row["addr_street2"] else None,
        "addr_city": row["addr_city"] if "addr_city" in row_keys and row["addr_city"] else None,
        "addr_state": row["addr_state"] if "addr_state" in row_keys and row["addr_state"] else None,
        "addr_zip": row["addr_zip"] if "addr_zip" in row_keys and row["addr_zip"] else None,
    }

    payment_info = {
        "cc_name": row["cc_name"] if "cc_name" in row_keys and row["cc_name"] else None,
        "cc_last4": row["cc_last4"] if "cc_last4" in row_keys and row["cc_last4"] else None,
        "cc_expiry": row["cc_expiry"] if "cc_expiry" in row_keys and row["cc_expiry"] else None,
        "payment_transaction_id": row["payment_transaction_id"] if "payment_transaction_id" in row_keys and row["payment_transaction_id"] else None,
    }

    # Order is complete when user_id, contact_info, and cc_info are fully provided
    is_complete = len(_get_missing_order_fields(row)) == 0

    return {
        "id": order_id,
        "user_id": user_id,
        "seats": seats,
        "status": row["status"] if "status" in row_keys else None,
        "is_complete": is_complete,
        "contact_info": contact_info,
        "payment_info": payment_info,
        "status_history": resolved_history,
        "tickets": tickets,
        "held_at": str(row["held_at"]) if "held_at" in row_keys and row["held_at"] else None,
        "hold_expires_at": str(row["hold_expires_at"]) if "hold_expires_at" in row_keys and row["hold_expires_at"] else None,
        "expired_at": str(row["expired_at"]) if "expired_at" in row_keys and row["expired_at"] else None,
        "processed_at": str(row["processed_at"]) if "processed_at" in row_keys and row["processed_at"] else None,
        "delivered_at": str(row["delivered_at"]) if "delivered_at" in row_keys and row["delivered_at"] else None,
        "created_at": str(row["created_at"]) if "created_at" in row_keys else None,
        "updated_at": str(row["updated_at"]) if "updated_at" in row_keys else None
    }
