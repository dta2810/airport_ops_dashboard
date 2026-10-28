"""Helpers for gold sink: key extraction, literal IN list building, safe quoting."""
from typing import Set, List, Tuple, Any
from datetime import date, datetime, timedelta


def extract_touched_flights(df_batch):
    """Extract flight_ids from milestone events in the batch."""
    return set(df_batch.select("flight_id").distinct().collect_list("flight_id"))


def extract_touched_keys_per_grain(df_batch, schema_name):
    """
    Extract touched keys per grain from a milestone batch.
    Returns: dict of grain -> set of key tuples

    Per contract §5:
    - flight_state: flight_id from events + next flight of same tail on IN_BLOCK
    - airport_hour_kpis: (origin, hour(std)) and (destination, hour(sta))
    - gate_hour_occupancy: (origin, gate_dep, each hour) + old gate if changed
    - hub_connections: outbound std hour of connections involving touched flights
    """
    from pyspark.sql import functions as F

    # Collect touched flights as dict for easy access
    batch_dict = df_batch.collect()
    touched_flight_ids = {row["flight_id"] for row in batch_dict}

    keys_by_grain = {
        "flight": set(),
        "airport_hour": set(),
        "gate_hour": set(),
        "hub_connections": set(),
    }

    # Extract flight_state keys (+ next flight on IN_BLOCK for turnaround)
    for row in batch_dict:
        keys_by_grain["flight"].add((row["flight_id"],))

    # Add next flight for turnaround expansion (IN_BLOCK events)
    # This requires a SQL query to find the next flight of the same tail
    if any(row["event_type"] == "IN_BLOCK" for row in batch_dict):
        tail_numbers = {row["tail_number"] for row in batch_dict if row.get("tail_number")}
        if tail_numbers:
            # Query to find next flight (will be done separately)
            pass

    # Extract airport_hour_kpis keys (origin+hour(std), destination+hour(sta))
    for row in batch_dict:
        if row.get("origin_iata") and row.get("std_utc"):
            hour = datetime.fromisoformat(str(row["std_utc"])).replace(minute=0, second=0, microsecond=0)
            keys_by_grain["airport_hour"].add((row["origin_iata"], hour))
        if row.get("destination_iata") and row.get("sta_utc"):
            hour = datetime.fromisoformat(str(row["sta_utc"])).replace(minute=0, second=0, microsecond=0)
            keys_by_grain["airport_hour"].add((row["destination_iata"], hour))

    # Gate hour occupancy: more complex, requires crossing touched flights with dim_gate
    # For now, extract from touched flights (will be expanded in SQL query)

    # Hub connections: from dim_connection where inbound or outbound is touched

    return keys_by_grain


def quote_literal(val):
    """Safe SQL literal quoting for IN lists."""
    if val is None:
        return "NULL"
    elif isinstance(val, bool):
        return "true" if val else "false"
    elif isinstance(val, (int, float)):
        return str(val)
    elif isinstance(val, (date, datetime)):
        if isinstance(val, datetime):
            return f"timestamp '{val.isoformat()}'"
        else:
            return f"date '{val}'"
    elif isinstance(val, str):
        # Escape single quotes
        escaped = val.replace("'", "''")
        return f"'{escaped}'"
    else:
        raise ValueError(f"Unsupported type for literal: {type(val)}")


def build_literal_in_list(values: List[Any], max_items_per_chunk=1000) -> List[str]:
    """
    Build literal IN clauses from a list of values, chunked if needed.
    Returns list of IN clause strings: ["(...)", "(...)", ...]
    """
    if not values:
        return []

    clauses = []
    for i in range(0, len(values), max_items_per_chunk):
        chunk = values[i : i + max_items_per_chunk]
        literals = [quote_literal(v) for v in chunk]
        clauses.append(f"({', '.join(literals)})")
    return clauses


def build_composite_key_in_list(key_tuples: List[Tuple], max_items_per_chunk=500) -> List[str]:
    """
    Build composite key IN clauses: (col1, col2, ...) IN ((val1, val2, ...), ...)
    Returns list of IN clause strings for chunking.
    """
    if not key_tuples:
        return []

    clauses = []
    for i in range(0, len(key_tuples), max_items_per_chunk):
        chunk = key_tuples[i : i + max_items_per_chunk]
        tuple_strs = [
            f"({', '.join(quote_literal(v) for v in key_tuple)})"
            for key_tuple in chunk
        ]
        clauses.append(f"({', '.join(tuple_strs)})")
    return clauses
