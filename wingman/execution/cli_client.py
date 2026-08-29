"""
Submit orders through the locally-authenticated Alpaca CLI.

Why the CLI and not alpaca-py's TradingClient: the CLI holds the auth session
for this machine, and the confirmed-working submission path for multi-leg
orders is piping a JSON file into `alpaca api POST /v2/orders`.

Why a temp file + pipe instead of `--legs "<json>"`: Windows Command Prompt
mangles escaped-quote JSON passed as a flag value (tested — it fails with
"system cannot find file specified"). Writing the payload to a file and
piping it through is the only reliable route on this platform.
"""

import json
import os
import subprocess
import tempfile


def submit_order(payload: dict, dry_run: bool) -> dict:
    """
    Submit `payload` (an mleg /v2/orders body from order_builder) via the CLI.

    Dry-run semantics: the CLI's --dry-run flag only exists on the single-leg
    `alpaca order submit` path; the raw-API route used for mleg has no tested
    equivalent. So for these (always-mleg) payloads, dry_run=True prints the
    payload and skips the subprocess entirely — nothing touches the API.
    (If a single-leg path is ever added, prefer
    `alpaca order submit --symbol ... --dry-run` there instead.)

    Returns a dict that is always safe to json-serialize into the daily log:
      {"success": bool, "dry_run": bool, "payload": ...,
       "response": parsed JSON or raw stdout, "stderr": ..., "returncode": ...}
    """
    if dry_run:
        print("[cli_client] DRY RUN — payload NOT submitted:")
        print(json.dumps(payload, indent=2))
        return {"success": True, "dry_run": True, "payload": payload,
                "response": None, "stderr": None, "returncode": None}

    # Write the payload where `type` can read it. delete=False because the
    # subprocess needs to open it after we close it (Windows won't allow a
    # second open of a still-open NamedTemporaryFile).
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", delete=False, encoding="utf-8"
    )
    try:
        json.dump(payload, tmp)
        tmp.close()

        # Windows-specific: `type` is cmd.exe's `cat`, and shell=True is
        # required for the pipe (`|`) to be interpreted by the shell at all.
        # On Unix this line would be: f'cat "{tmp.name}" | alpaca api POST /v2/orders'
        cmd = f'type "{tmp.name}" | alpaca api POST /v2/orders'
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=60
        )

        response: object
        try:
            response = json.loads(result.stdout) if result.stdout.strip() else None
        except json.JSONDecodeError:
            response = result.stdout  # keep the raw text for the log

        # The CLI exits non-zero on transport errors; an API-level rejection
        # comes back as JSON with no "id". Treat both as failure.
        success = (
            result.returncode == 0
            and isinstance(response, dict)
            and "id" in response
        )
        return {
            "success": success,
            "dry_run": False,
            "payload": payload,
            "response": response,
            "stderr": result.stderr.strip() or None,
            "returncode": result.returncode,
        }
    except Exception as exc:  # noqa: BLE001 — submission must never crash the loop
        return {"success": False, "dry_run": False, "payload": payload,
                "response": None, "stderr": f"{type(exc).__name__}: {exc}",
                "returncode": None}
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass  # a leaked temp file is not worth failing a cycle over
