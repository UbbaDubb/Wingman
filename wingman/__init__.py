"""
Wingspan — SPY volatility-trading agent.

IMPORTANT — how to run:
    From the REPO ROOT (the directory containing wingman/), run:
        python -m wingman.loop

    Do NOT run scripts from inside the wingman/ directory itself. This package
    contains a subpackage named `logging`, and when Python's working/script
    directory is wingman/, that subpackage shadows the standard library's
    `logging` module — which crashes every third-party import (scipy,
    alpaca-py, requests all do `import logging` at import time). Running as a
    package (`python -m wingman.loop`) keeps the top-level name `wingman` on
    sys.path instead, so stdlib `logging` resolves correctly.
"""
