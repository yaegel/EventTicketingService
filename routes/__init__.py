"""
Master routes package aggregator.
Combines modular route blueprints for:
- Seat reservation (seat_reservation_routes.py)
- Payment processing & refunds (payment_routes.py)
- Order lifecycle, updates & checkout management (order_routes.py)
- Ticket generation & delivery (ticket_routes.py)
"""
from flask import Blueprint
from .seat_reservation_routes import seat_reservation_bp
from .payment_routes import payment_bp
from .order_routes import order_bp
from .ticket_routes import ticket_bp
from .routes_common import _serialize_order, _extract_order_updates, _get_missing_order_fields, _parse_seats

api_bp = Blueprint("api", __name__)

# Register all modular sub-blueprints onto api_bp
api_bp.register_blueprint(seat_reservation_bp)
api_bp.register_blueprint(payment_bp)
api_bp.register_blueprint(order_bp)
api_bp.register_blueprint(ticket_bp)

__all__ = [
    "api_bp",
    "seat_reservation_bp",
    "payment_bp",
    "order_bp",
    "ticket_bp",
    "_serialize_order",
    "_extract_order_updates",
    "_get_missing_order_fields",
    "_parse_seats",
]
