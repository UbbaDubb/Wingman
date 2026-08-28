def build_vertical(*args, **kwargs):
    """
    Build an order payload for a vertical spread (long/short call or put
    spread) suitable for submit_order.
    """
    raise NotImplementedError("TODO: implement")


def build_straddle(*args, **kwargs):
    """
    Build an order payload for a straddle (long/short call + put at the
    same strike) suitable for submit_order.
    """
    raise NotImplementedError("TODO: implement")
