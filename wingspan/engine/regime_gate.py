def check_regime(context: dict) -> dict:
    """
    Ask the Anthropic API to assess the current market regime and gate the
    proposed trade's sizing accordingly, before it reaches execution.

    Args:
        context: Relevant market/decision context to send to the model
        (e.g. snapshot summary, proposed trade, recent regime history).

    Returns:
        dict: {"decision": "full" | "half" | "stand_down", "rationale": str}
    """
    raise NotImplementedError("TODO: implement")
