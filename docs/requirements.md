# Requirements

## Functional

- FR1 — Show live airport operations for one central hub and its spoke airports: flights by phase (scheduled, boarding, taxi-out, airborne, taxi-in, at gate), movements per hour, OTP D0/D15, taxi-out/in, gate occupancy, turnaround vs minimum ground time, delay minutes by cause, cancellations/diversions, hub connections at risk.
- FR2 — Refresh the dashboard every 30 seconds without user action.
- FR3 — Simulate a realistic event feed: historical backfill plus a live emitter on an accelerated clock.
- FR4 — Keep a governed copy of every gold table in Unity Catalog (Delta replica).

## Non-functional

- NFR1 — Freshness: event → Lakebase p95 ≤ 30 s (F0 measured p95 9.4 s on the probe).
- NFR2 — Serverless compute only; no always-on SQL warehouse in the serving path.
- NFR3 — The bronze pipeline is treated as customer-owned and is never modified; gold is a separate consumer.
- NFR4 — Silver may be a regular view without Change Data Feed.
- NFR5 — Gold keys may be composite and time-based; writes are idempotent per micro-batch and propagate deletes.
- NFR6 — Pipeline runs as a service principal with least privilege; OAuth only, no stored secrets.
- NFR7 — Deployable end to end in another workspace from this repo (bundle + one setup job).
- NFR8 — Fictitious brand only; no customer names, logos or data.
