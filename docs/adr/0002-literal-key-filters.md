# ADR 0002 — Literal key lists and grain columns on the fact table

Status: accepted (2026-10-15)

`IN (subquery)` does not push key filters into the event-log scan below the silver window; a literal `IN (...)` list does. Filters only prune tables that carry the filtered columns, so the milestone fact carries `event_date` and airport columns and is clustered on them. Composite keys recompute whole touched groups.
