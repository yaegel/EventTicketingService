# Event Ticketing Service

A lightweight Flask web service enforcing order state transitions with a local SQLite database and a background worker thread.

---

## Architecture Overview

- **Web Framework**: Flask (Python 3.11)
- **Database**: SQLite with Write-Ahead Logging (`WAL` mode) for concurrent read/write support between request threads and the background worker.
- **State Machine**: Enforces valid transitions and rejects invalid state changes.
- **Background Worker**: A daemon thread continuously querying and processing the oldest unprocessed order in the database.
- **Containerization**: Docker & Docker Compose.

---

## State Machine

```
                          +----------------------+
                          |         held         | (seats reserved with TTL)
                          +----+------------+----+
                               |            |
                   +-----------+            +----------------+
                   | (Provide Billing)                       |
                   v                                         |
       +-----------------------+                             |
       |      initialized      |                             |
       +---+---------------+---+                             |
           |               |                                 |
     (Authorize)       (Cancel)                              |
           |               |                                 v
           | (Decline)     v                        +-----------------+
           |   +---> +---------------+              |     expired     | (terminal; cleaned up
           |   |     | payment_failed|              +-----------------+  by worker after retention)
           |   |     +-------+-------+                       ^
           |   |             | (Cancel: user lacks funds)    |
           v   |             v                               |
 +-------------------+ +---------------+                     |
 |payment_authorized | |   cancelled   | (unpaid terminal)   |
 +---+------------+--+ +---+---+---+---+                     |
     |            |        |   |   |                         |
 (Success) (Delivery Fail) |   |   +--(Close)----+           |
     |            |        |   |                 |           |
     |            v        |   |                 |           |
     |  +------------------------+               |           |
     |  | ticket_delivery_failed |               |           |
     |  +---------+--------------+               |           |
     |            |                              |           |
     |            v                              |           |
     |  +-----------------------+                |           |
     |  |needs_human_resolution |                |           |
     |  +-----+-----+-----------+                |           |
     |        |     |                            |           |
     | (Resolve) (Cancel)                        |           |
     |        |     |                            |           |
     v        v     +------------>--+            |           |
 +--------------+                   |            |           |
 |   complete   +-------------------+            |           |
 +--------------+ (Refund initiated)             |           |
        |                                        |           |
        v                                        |           |
 +---------------+                               |           |
 |   cancelled   |                               |           |
 +---+-------+---+                               |           |
     |       |                                   |           |
 (Success) (Fail)                                |           |
     |       |                                   |           |
     v       v                                   |           |
 +----------+ +-------------------+              |           |
 | refunded | |   refund_failed   |              |           |
 +----+-----+ +---------+---------+              |           |
      |                 |                        |           |
      |                 v                        |           |
      |       +-------------------------+        |           |
      |       | needs_human_resolution  |        |           |
      |       +-------------------------+        |           |
      v                                          v           |
 +--------------------------------------------------+        |
 |                      closed                      |        | (terminal)
 +--------------------------------------------------+        |
          +------- Holds expire after seat_hold_ttl ---------+
```

### Order Lifecycle & State Flows
1. **Happy Path**: `held` &rarr; `initialized` &rarr; `payment_authorized` &rarr; `complete`
2. **Cancellation & Refund**: `complete` &rarr; `cancelled` &rarr; `refunded` &rarr; `closed`
3. **Failed Refund Escalation**: `complete` &rarr; `cancelled` &rarr; `refund_failed` &rarr; `needs_human_resolution`
4. **Ticket Delivery Failure & Resolution**: `payment_authorized` &rarr; `ticket_delivery_failed` &rarr; `needs_human_resolution` &rarr; `complete` (or `cancelled`)
5. **Human Resolution via Cancellation & Refund**: `needs_human_resolution` &rarr; `cancelled` &rarr; `refunded` &rarr; `closed`
6. **Payment Decline Recovery**: `initialized` &rarr; `payment_failed` &rarr; `held` &rarr; `initialized` &rarr; `payment_authorized` &rarr; `complete`
7. **Payment Decline Cancellation**: `initialized` &rarr; `payment_failed` &rarr; `cancelled` (user realizes they lack funds)
8. **Pre-Payment Cancellation**: `held` &rarr; `cancelled`
9. **Cancelled Order Archival / Closure**: `cancelled` &rarr; `closed`

### Terminal & Expiration States
- `complete`: Fulfillment finished (tickets generated and delivered).
- `closed`: Terminal state for cancelled orders (both refunded and administratively archived).
- `needs_human_resolution`: Escalation state when refund or ticket delivery fails; only advances to `complete` or `cancelled`.
- `cancelled`: Intermediate or terminal state for cancelled orders (`held`, `initialized`, `payment_failed`, or post-payment cancellation awaiting refund/closure).
- `expired`: Seat hold exceeded `Config.SEAT_HOLD_TTL` minutes without completion. Worker purges expired orders after `Config.EXPIRED_RETENTION_TTL` minutes.

---

## Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/claim_seats` | Claims seats and reserves a temporary hold (`held`), setting `held_at` and `hold_expires_at` for a user or guest user. |
| `PATCH`/`PUT`/`POST` | `/orders/<id>` | General order update: replaces guest `user_id`, adds/updates `contact_info` and/or payment `payment_info` / `cc_info`. Automatically advances to `initialized` once all details are complete. |
| `POST` | `/orders/<id>/process_payment` | Authorizes order charge through payment gateway (`held -> initialized -> payment_authorized` or `payment_failed`). |
| `POST` | `/orders/<id>/deliver_tickets` | Delivers tickets to customer via email/SMS (auto-creates tickets if missing) (`payment_authorized -> complete`, or `ticket_delivery_failed -> needs_human_resolution`). |
| `GET` | `/orders/<id>/tickets` | Retrieves all generated tickets for a specific order. |
| `POST` | `/orders/<id>/refund` | Refunds an order (`complete` or `needs_human_resolution` &rarr; `cancelled` &rarr; `refunded` &rarr; `closed` on success, or `cancelled` &rarr; `refund_failed` &rarr; `needs_human_resolution` on failure). |
| `POST` | `/orders/<id>/resolve` | Resolves an order in `needs_human_resolution` to `complete` or `cancelled`. |
| `POST` | `/orders/<id>/close` | Administratively closes an order in `cancelled` or `refunded` to terminal `closed`. |
| `POST` | `/cancel_order` | Cancels an unpaid order (`held`, `payment_failed` &rarr; `cancelled`). Completed orders must be refunded via `/orders/<id>/refund`. |
| `GET` | `/orders` | Lists all orders with their current statuses, status transition history, contact/cc details, and hold timestamps. |
| `GET` | `/orders/<id>` | Retrieves details of a specific order including complete status transition history. |
| `GET` | `/health` | Service health check. |

---

### Payment Gateway Test Harness Rules

The stubbed gateway ([`stub_payment_gateway.py`](file:///c:/Projects/EventTicketingService/stub_payment_gateway.py)) simulates credit card outcomes using repeating-digit card numbers:

| Card Number Pattern | Gateway Outcome | Next Order State | Description |
|---|---|---|---|
| Card ending in `0000` | Auth: Declined (`HTTP 402`) | `payment_failed` | Insufficient funds |
| Card ending in `1111` | Auth: Declined (`HTTP 402`) | `payment_failed` | Expired card |
| Card ending in `2222` | Auth: Declined (`HTTP 402`) | `payment_failed` | Suspected fraud |
| Card ending in `3333` | Auth: Declined (`HTTP 402`) | `payment_failed` | Invalid verification code |
| Card ending in `7777` | Auth: Approved (`HTTP 200`)<br>Refund: Error (`HTTP 402`) | Auth: `payment_authorized`<br>Refund: `needs_human_resolution` | Network timeout during return of funds |
| Card ending in `8888` | Auth: Approved (`HTTP 200`)<br>Refund: Declined (`HTTP 402`) | Auth: `payment_authorized`<br>Refund: `needs_human_resolution` | Issuer rejected return of funds (closed account) |
| Card ending in `9999` | Auth: Error (`HTTP 402`) | `payment_failed` | Gateway timeout during authorization |
| Any other card (e.g. `...4242`) | Auth: Approved (`HTTP 200`)<br>Refund: Approved (`HTTP 200`) | Auth: `payment_authorized`<br>Refund: `cancelled` | Normal successful charge and refund |

---

## Quickstart with Docker Compose

### 1. Build and Run
```bash
docker compose up --build
```

The service will start on `http://localhost:5000`.

### 2. Verify Health
```bash
curl http://localhost:5000/health
```

---

## Sample API Requests

### 1. Claim Seats (`held`)
```bash
curl -X POST http://localhost:5000/claim_seats \
  -H "Content-Type: application/json" \
  -d '{"user_id": "user_42", "seats": ["A1", "A2"]}'
```
Response:
```json
{
  "message": "Seats held successfully for 10.0 minutes.",
  "order": {
    "id": "01a0e2a7-4732-73c8-ba3f-1963ac5e05bd",
    "user_id": "user_42",
    "seats": ["A1", "A2"],
    "status": "held",
    "held_at": "2026-09-27 11:15:00",
    "hold_expires_at": "2026-09-27 11:25:00",
    "expired_at": null,
    "processed_at": null,
    "created_at": "...",
    "updated_at": "..."
  }
}
```

### 2. Provide Contact & Billing Details
```bash
curl -X PATCH http://localhost:5000/orders/01a0e2a7-4732-73c8-ba3f-1963ac5e05bd \
  -H "Content-Type: application/json" \
  -d '{
    "contact_info": {
      "fname": "Alice",
      "lname": "Smith",
      "email": "alice@example.com",
      "phone": "555-0100"
    },
    "payment_info": {
      "cc_name": "Alice Smith",
      "cc_number": "4111111111114242",
      "cc_expiry": "12/28"
    }
  }'
```

### 3. Process Payment (`held -> initialized -> payment_authorized`)
```bash
curl -X POST http://localhost:5000/orders/01a0e2a7-4732-73c8-ba3f-1963ac5e05bd/process_payment
```

### 4. Deliver Tickets (`payment_authorized -> complete`)
```bash
curl -X POST http://localhost:5000/orders/01a0e2a7-4732-73c8-ba3f-1963ac5e05bd/deliver_tickets
```

### 5. Attempt Invalid State Transition (Example: cancelling a completed order)
```bash
curl -X POST http://localhost:5000/cancel_order \
  -H "Content-Type: application/json" \
  -d '{"order_id": "01a0e2a7-4732-73c8-ba3f-1963ac5e05bd"}'
```
Response (HTTP 400):
```json
{
  "attempted_target_state": "cancelled",
  "current_state": "complete",
  "error": "Invalid state transition: Cannot transition order from 'complete' to 'cancelled'."
}
```

### 6. Inspect All Orders
```bash
curl http://localhost:5000/orders
```

---

## Background Worker

The background worker runs as a daemon thread in `worker.py`. 
- **Polling interval**: Configurable via `WORKER_POLL_INTERVAL_SECONDS` (default: 3.0s).
- **Seat hold expiration**: Identifies orders in `held` where `hold_expires_at <= now`, sets status to `expired`, and records `expired_at`.
- **Database cleanup**: Permanently deletes records in `expired` status whose retention period (`config.expired_retention_ttl`, default: 5.0 minutes) has elapsed.
- **Order processing & fulfillment pipeline**: Queries the oldest active order (`initialized`, `payment_authorized`, or `complete`) where `processed_at IS NULL`. The worker orchestrates fulfillment without worker-level sleep loops, calling the real API endpoints:
  1. `POST /orders/<id>/process_payment`: Authorizes payment through `StubPaymentGateway` (with simulated gateway delay).
  2. `POST /orders/<id>/deliver_tickets`: Creates tickets (if missing) and dispatches them to customer, stamps `delivered_at`, and transitions status to `complete` (with simulated dispatch delay).
  3. Marks `processed_at = CURRENT_TIMESTAMP`.
- Watch the container logs to observe worker activity in real time:
  ```bash
  docker compose logs -f
  ```

---

## Running Test Cases in Docker

The test suite in [`test_cases/`](test_cases/) covers 5 end-to-end paths through the state machine. Each test executes against an isolated, temporary SQLite database.

### 1. Build and Start the Container
Ensure the container image contains the latest code and test files:
```bash
docker compose up --build -d
```

### 2. Run All 5 State Path Tests in Container
```bash
docker compose exec web python -m unittest test_cases/test_all_paths.py
```

*(Or spin up a one-off container: `docker compose run --rm web python -m unittest test_cases/test_all_paths.py`)*

### 3. Run Individual Path Tests
```bash
# Path 1: Happy Path (held -> initialized -> payment_authorized -> complete)
docker compose exec web python -m unittest test_cases/test_1_happy_path.py

# Path 2: Payment Decline & Recovery (held -> initialized -> payment_failed -> held -> initialized -> payment_authorized -> complete)
docker compose exec web python -m unittest test_cases/test_2_payment_failure_recovery.py

# Path 3: Pre-Payment Voluntary Cancellation (held -> cancelled)
docker compose exec web python -m unittest test_cases/test_3_voluntary_cancellation.py

# Path 4: Post-Payment Refund & Guard Enforcement (held -> initialized -> payment_authorized -> cancelled)
docker compose exec web python -m unittest test_cases/test_4_refund_cancellation.py

# Path 5: Seat Hold Expiration & Cart Abandonment (held -> expired)
docker compose exec web python -m unittest test_cases/test_5_hold_expiration.py
```

