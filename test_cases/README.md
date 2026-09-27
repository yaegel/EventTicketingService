# State Machine Path Test Cases

This directory contains **5 end-to-end integration test suites** exercising distinct pathways through the order lifecycle state machine. Each test runs against an isolated, temporary SQLite database and asserts on HTTP responses, status transitions, domain guards, and full audit histories in `order_status`.

---

## State Transition Matrix & Tested Paths

```
                         +----------------------+
                         |         held         |
                         +----+------------+----+
                              |            |
                  +-----------+            +----------------+
                  | (Path 1, 2, 4)                          | (Path 5)
                  v                                         v
      +-----------------------+                    +-----------------+
      |      initialized      |                    |     expired     | (terminal)
      +---+---------------+---+                    +-----------------+
          |               |
    (Path 1, 4)     (Path 2: Fail)
          |               v
          |       +---------------+
          |       | payment_failed|
          |       +-------+-------+
          |               | (Retry)
          |               +------------+
          |                            | (User realizes no funds)
          v                            v
+-------------------+          +---------------+
|payment_authorized |          |   cancelled   | (unpaid cancelled)
+---+------------+--+          +---+---+---+---+
    |            |                 |   |   |
(Success) (Delivery Fail)          |   |   +--(Close)--------------------+
    |            |                 |   |                                 |
    |            v                 |   |                                 |
    |  +------------------------+  |   |                                 |
    |  | ticket_delivery_failed |  |   |                                 |
    |  +---------+--------------+  |   |                                 |
    |            |                 |   |                                 |
    |            v                 |   |                                 |
    |  +-----------------------+   |   |                                 |
    |  |needs_human_resolution |   |   |                                 |
    |  +-----+-----+-----------+   |   |                                 |
    |        |     |               |   |                                 |
    | (Resolve) (Cancel)           |   |                                 |
    |        |     |               |   |                                 |
    v        v     +------------>--+   |                                 |
+--------------+                       |                                 |
|   complete   +-----------------------+                                 |
+--------------+ (Refund initiated: complete -> cancelled)               |
                                       |                                 |
                                       v                                 |
                                +---------------+                        |
                                |   cancelled   |                        |
                                +---+-------+---+                        |
                                    |       |                            |
                                (Success) (Fail)                         |
                                    |       |                            |
                                    v       v                            |
                                +----------+ +-------------------+       |
                                | refunded | |   refund_failed   |       |
                                +----+-----+ +---------+---------+       |
                                     |                 |                 |
                                     |                 v                 |
                                     |       +-------------------------+ |
                                     |       | needs_human_resolution  | |
                                     |       +-------------------------+ |
                                     v                                   v
                        +---------------------------------------------------+
                        |                      closed                       | (terminal)
                        +---------------------------------------------------+
```

---

## Overview of the Test Cases

| # | Test File | State Path | Description & Guards Exercised |
|---|---|---|---|
| **1** | [`test_1_happy_path.py`](file:///c:/Projects/EventTicketingService/test_cases/test_1_happy_path.py) | `held` &rarr; `initialized` &rarr; `payment_authorized` &rarr; `complete` | **The Happy Path**: Reserving seats, providing contact & CC details, charging valid card via payment gateway (`held -> initialized -> payment_authorized`), delivering tickets. Verifies completed orders cannot be cancelled directly without refund flow. |
| **2** | [`test_2_payment_failure_recovery.py`](file:///c:/Projects/EventTicketingService/test_cases/test_2_payment_failure_recovery.py) | `held` &rarr; `initialized` &rarr; `payment_failed` &rarr; `held` &rarr; `initialized` &rarr; `payment_authorized` &rarr; `complete` | **Payment Decline & Recovery**: Card ending in `0000` is declined (Insufficient Funds, `HTTP 402`). Customer updates card to `...4242` (resetting to `held`), re-processes payment, authorizes, and completes. |
| **3** | [`test_3_voluntary_cancellation.py`](file:///c:/Projects/EventTicketingService/test_cases/test_3_voluntary_cancellation.py) | `held` &rarr; `cancelled` | **Pre-Payment Voluntary Cancellation**: Customer A holds seats; Customer B is blocked (`HTTP 409 Conflict`). Customer A cancels order via `/cancel_order`. Seats are instantly released and Customer B successfully books them. |
| **4** | [`test_4_refund_cancellation.py`](file:///c:/Projects/EventTicketingService/test_cases/test_4_refund_cancellation.py) | `complete` &rarr; `cancelled` &rarr; `refunded` &rarr; `closed`<br>or `refund_failed` &rarr; `needs_human_resolution`<br>and `payment_failed` &rarr; `cancelled` | **Real-World Refund & Cancellation**: 1) Successful refund transitions `complete -> cancelled -> refunded -> closed`. 2) Failed refund (card 8888) transitions `complete -> cancelled -> refund_failed -> needs_human_resolution`. 3) Payment failed cancellation transitions `payment_failed -> cancelled`. 4) Invariants: `payment_authorized` strictly transitions to `complete`. |
| **5** | [`test_5_hold_expiration.py`](file:///c:/Projects/EventTicketingService/test_cases/test_5_hold_expiration.py) | `held` &rarr; `expired` | **Hold Expiration & Cart Abandonment**: Simulates passage of time past `Config.SEAT_HOLD_TTL`. Assert that payment attempts reject expired holds via `_guard_held_not_expired`, and background worker's `expire_stale_holds()` transitions abandoned holds to `expired`. |
| **6** | [`test_6_worker_processing.py`](file:///c:/Projects/EventTicketingService/test_cases/test_6_worker_processing.py) | 6A: `held` &rarr; `initialized` &rarr; `payment_authorized` &rarr; `complete`<br>6B: `held` &rarr; `initialized` &rarr; `payment_failed` | **Worker Orchestration**: Worker autonomously picks up oldest unprocessed records, calls payment endpoint, generates tickets, and delivers them. |
| **7** | [`test_7_ticket_delivery_failure_and_resolution.py`](file:///c:/Projects/EventTicketingService/test_cases/test_7_ticket_delivery_failure_and_resolution.py) | 7A: `...` &rarr; `needs_human_resolution` &rarr; `complete`<br>7B: `...` &rarr; `needs_human_resolution` &rarr; `cancelled` &rarr; `closed`<br>7C: `...` &rarr; `needs_human_resolution` &rarr; `cancelled` &rarr; `refunded` &rarr; `closed`<br>7D: `held` &rarr; `cancelled` &rarr; `closed` | **Delivery Failure & Resolution**: 7A) Delivery failure escalates to human resolution, then re-delivers to complete. 7B) Delivery failure resolved to closed via cancelled. 7C) Delivery failure refunded via cancelled -> refunded -> closed. 7D) Unpaid cancelled order closed. |

---

## Detailed Test Walkthroughs

### 1. Happy Path ([`test_1_happy_path.py`](file:///c:/Projects/EventTicketingService/test_cases/test_1_happy_path.py))
- **Step 1 (`POST /claim_seats`)**: Customer `alice_smith` reserves seats `["A1", "A2"]`. System records initial status `held` with `held_at` and `hold_expires_at`.
- **Step 2 (`PATCH /orders/<id>`)**: Submits full contact and payment information (card `...4242`).
- **Step 3 (`POST /orders/<id>/process_payment`)**: Invokes gateway authorization. Order transitions `held -> initialized -> payment_authorized`. Gateway approves the charge ($100.00) and returns transaction ID.
- **Step 4 (`POST /orders/<id>/deliver_tickets`)**: Finalizes order, generates tickets, and delivers them to customer, transitioning to `complete`.
- **Step 5 (Invariants Check)**: Calling `POST /cancel_order` on completed order is rejected with `HTTP 400` (`InvalidStateTransitionError`).
- **Audit Assert**: `status_history == ["held", "initialized", "payment_authorized", "complete"]`.

---

### 2. Payment Failure & Recovery ([`test_2_payment_failure_recovery.py`](file:///c:/Projects/EventTicketingService/test_cases/test_2_payment_failure_recovery.py))
- **Step 1 (`POST /claim_seats`)**: Customer `bob_builder` reserves seat `["B1"]` &rarr; `held`.
- **Step 2 (`PATCH /orders/<id>`)**: Submits contact info with test card ending in `0000` (Insufficient Funds trigger).
- **Step 3 (`POST /orders/<id>/process_payment`)**: Gateway declines authorization. Transitions `held -> initialized -> payment_failed` (`HTTP 402`).
- **Step 4 (`PATCH /orders/<id>`)**: Customer updates credit card with working card `...4242`, resetting order status to `initialized` (via `held`).
- **Step 5 (`POST /orders/<id>/process_payment`)**: Authorizes with new card, transitioning `held -> initialized -> payment_authorized`.
- **Step 6 (`POST /orders/<id>/deliver_tickets`)**: Generates and delivers tickets, completing order (`complete`).
- **Audit Assert**: `status_history == ["held", "initialized", "payment_failed", "held", "initialized", "payment_authorized", "complete"]`.

---

### 3. Voluntary Cancellation ([`test_3_voluntary_cancellation.py`](file:///c:/Projects/EventTicketingService/test_cases/test_3_voluntary_cancellation.py))
- **Step 1 (`POST /claim_seats`)**: Customer A reserves seats `["C1", "C2"]` &rarr; `held`.
- **Step 2 (`POST /claim_seats`)**: Customer B attempts to claim `["C1"]` &rarr; Rejected with `HTTP 409 Conflict`.
- **Step 3 (`POST /cancel_order`)**: Customer A cancels order &rarr; Transitions to `cancelled` (terminal).
- **Step 4 (Invariants Check)**: Calling `POST /orders/<id>/process_payment` on cancelled order returns `HTTP 400`.
- **Step 5 (`POST /claim_seats`)**: Customer B retries claiming `["C1", "C2"]` &rarr; Successfully claimed (`HTTP 201`).
- **Audit Assert**: `status_history == ["held", "cancelled"]`.

---

### 4. Real-World Refund, Escalation, & Cancellation ([`test_4_refund_cancellation.py`](file:///c:/Projects/EventTicketingService/test_cases/test_4_refund_cancellation.py))
- **Test 4A (Successful Refund)**: Completed order `held -> initialized -> payment_authorized -> complete`. Calling `POST /orders/<id>/refund` transitions `complete -> cancelled -> refunded -> closed`. Tickets are voided and seats released.
- **Test 4B (Failed Refund Escalation)**: Completed order with card ending in `8888`. Refund is declined by card issuer (`HTTP 402`). Order transitions `complete -> cancelled -> refund_failed -> needs_human_resolution`.
- **Test 4C (Payment Failed Cancellation)**: Order reaches `payment_failed`. Customer cancels via `/cancel_order`, transitioning `payment_failed -> cancelled`. Seats are immediately released.
- **Test 4D (Payment Authorized Invariants)**: Verifies that orders in `payment_authorized` reject refund and cancellation calls with `HTTP 400`. Must complete before refunding.

---

### 5. Seat Hold Expiration ([`test_5_hold_expiration.py`](file:///c:/Projects/EventTicketingService/test_cases/test_5_hold_expiration.py))
- **Step 1 (`POST /claim_seats`)**: Customer reserves seat `["E1"]` &rarr; `held`.
- **Step 2 (TTL Elapse)**: Hold timestamp `hold_expires_at` is set past `Config.seat_hold_ttl`.
- **Step 3 (Guard Check)**: Customer calls `POST /orders/<id>/process_payment`. Guard `_guard_held_not_expired` detects stale hold and transitions order to `expired` (`HTTP 400`).
- **Step 4 (Worker Expiration)**: A second abandoned hold `["E2"]` is detected and transitioned to `expired` by `worker_instance.expire_stale_holds()`.
- **Step 5 (Inventory Recovery)**: Customer `george_new` claims `["E1", "E2"]` successfully (`HTTP 201`).
- **Audit Assert**: `status_history == ["held", "expired"]`.

## How to Review and Run the Tests in Docker

### 1. Build and Start the Container
Ensure the Docker container image includes the latest code and test files:
```bash
docker compose up --build -d
```

### 2. Run All 5 State Path Tests in Container
```bash
docker compose exec web python -m unittest test_cases/test_all_paths.py
```

*(Alternatively, spin up a one-off test container: `docker compose run --rm web python -m unittest test_cases/test_all_paths.py`)*

### 3. Run Individual Test Suites
```bash
# Test 1: Happy Path
docker compose exec web python -m unittest test_cases/test_1_happy_path.py

# Test 2: Payment Failure & Recovery
docker compose exec web python -m unittest test_cases/test_2_payment_failure_recovery.py

# Test 3: Voluntary Cancellation
docker compose exec web python -m unittest test_cases/test_3_voluntary_cancellation.py

# Test 4: Refund Cancellation & Guard Enforcement
docker compose exec web python -m unittest test_cases/test_4_refund_cancellation.py

# Test 5: Seat Hold Expiration
docker compose exec web python -m unittest test_cases/test_5_hold_expiration.py
```

### 4. Running Locally (Without Docker)
If you prefer running outside Docker, you can run:
```bash
python -m unittest test_cases/test_all_paths.py
```
