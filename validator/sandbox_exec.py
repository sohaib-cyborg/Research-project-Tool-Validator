"""
The execution slice (part of S4, built now so replay can actually run).

In plain terms: take a Python script (a string), put it inside the running
container, execute it with a timeout, and hand back (exit_code, stdout).

We do NOT mount the host filesystem. The script is written into the container
via a base64 trick so we don't need volumes or a shell heredoc. This keeps the
host safe: the tool code only ever runs inside the disposable container.
"""
from __future__ import annotations

import base64

from .sandbox import Sandbox


def run_script_in_container(sb: Sandbox, script: str, timeout_s: int = 10) -> tuple[int, str]:
    """Write `script` into the container and run it. Returns (exit_code, output).

    Raises RuntimeError if the sandbox isn't available (no Docker).
    """
    if not sb.available or sb.container is None:
        raise RuntimeError("Sandbox not available; cannot execute script.")

    # Encode the script so no quoting/escaping can break the command.
    b64 = base64.b64encode(script.encode("utf-8")).decode("ascii")

    # Decode to a file inside the container, then run it with a hard timeout.
    # `timeout` (coreutils) kills runaway tools -> protects against infinite loops.
    cmd = [
        "sh", "-c",
        f"echo {b64} | base64 -d > /tmp/_tool_test.py && "
        f"timeout {timeout_s} python /tmp/_tool_test.py",
    ]
    exit_code, output = sb.exec(cmd)
    return exit_code, output


def container_identity(sb: Sandbox) -> dict:
    """Return proof of WHERE code ran: container id + hostname from inside it.

    The hostname printed here is the container's (a short hash), which differs
    from your host machine's hostname -- that difference is the proof the code
    executed in Docker and not on your laptop.
    """
    if not sb.available or sb.container is None:
        return {"docker": False}
    short_id = sb.container.id[:12]
    # Ask the container itself who it is.
    _, hostname = sb.exec(["sh", "-c", "hostname"])
    return {
        "docker": True,
        "container_id": short_id,
        "container_hostname": hostname.strip(),
    }
