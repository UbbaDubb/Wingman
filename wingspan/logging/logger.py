def log_cycle(record: dict) -> None:
    """
    Append one JSON line per cycle to logs/YYYY-MM-DD.jsonl (date determined
    at call time), recording the snapshot/decision/execution state for that
    cycle.

    Args:
        record: Cycle record to serialize and append.
    """
    raise NotImplementedError("TODO: implement")
