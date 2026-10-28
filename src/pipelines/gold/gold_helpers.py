"""Helpers for the gold sink: safe Spark SQL literal quoting."""
from datetime import date, datetime


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
        # Spark SQL literal: backslash is the escape character, so escape it first, then the quote
        escaped = val.replace("\\", "\\\\").replace("'", "\\'")
        return f"'{escaped}'"
    else:
        raise ValueError(f"Unsupported type for literal: {type(val)}")
