def submit_order(payload: dict, dry_run: bool) -> dict:
    """
    Submit an order to Alpaca via the authenticated Alpaca CLI rather than
    a direct API call.

    Windows-specific note: unlike a Unix pipeline (`cat file | alpaca api ...`),
    this should write `payload` to a temp JSON file and pipe it through via
    `type <file> | alpaca api POST /v2/orders` using subprocess, since `type`
    is the Command Prompt equivalent of `cat`.

    Args:
        payload: Order payload to submit.
        dry_run: If True, do not actually submit — log/return what would
        have been sent instead.

    Returns:
        dict: The CLI's parsed response (or the would-be payload, if dry_run).
    """
    raise NotImplementedError("TODO: implement")
