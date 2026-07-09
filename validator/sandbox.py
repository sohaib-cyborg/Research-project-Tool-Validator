"""
S0 -- Sandbox provisioning.

Design (per your choices):
  * The pipeline runs on the HOST and talks to Docker via the SDK (docker-py).
  * Each tool gets a FRESH, DISPOSABLE container from a versioned base image.
  * Isolation flags are applied here (network off, memory cap, dropped caps).
  * The container is destroyed after use; results are captured as data first.

We deliberately do NOT do ToolMaker-style within-tool checkpointing: we are
adjudicating, not iterating, so immutability (fresh container each time) gives
us reproducibility more simply than snapshot/restore.

This module degrades gracefully if no Docker daemon is reachable, so the rest
of the skeleton (S1 parse, which runs on the host) is still testable anywhere.
"""
from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass

try:
    import docker
    from docker.errors import DockerException
    _DOCKER_IMPORTABLE = True
except Exception:                       # pragma: no cover
    _DOCKER_IMPORTABLE = False


# The known-good base image. Built once, versioned, reused for every tool.
# Pin an explicit tag -- never `latest` -- so validation is reproducible.
BASE_IMAGE = "python:3.12-slim"

# Isolation profile for the disposable container.
SANDBOX_KWARGS = dict(
    network_disabled=True,              # no network by default (S4 opens it only if declared)
    mem_limit="512m",                   # memory cap
    nano_cpus=1_000_000_000,            # ~1 CPU
    cap_drop=["ALL"],                   # drop all Linux capabilities
    security_opt=["no-new-privileges"],
    read_only=False,                    # tmp workdir is writable; host FS is not mounted
    detach=True,
    tty=True,
    command="sleep 3600",               # keep alive; we exec into it
)


@dataclass
class Sandbox:
    """A handle to a live container (or a no-op stub when Docker is absent)."""
    container: object | None
    image: str
    available: bool

    def exec(self, cmd: list[str]) -> tuple[int, str]:
        """Run a command inside the container. Returns (exit_code, output)."""
        if not self.available or self.container is None:
            raise RuntimeError("Sandbox not available; cannot exec in container.")
        res = self.container.exec_run(cmd, demux=False)
        out = res.output.decode("utf-8", errors="replace") if res.output else ""
        return res.exit_code, out

    def destroy(self) -> None:
        if self.container is not None:
            with contextlib.suppress(Exception):
                self.container.remove(force=True)


def _client():
    if not _DOCKER_IMPORTABLE:
        return None
    try:
        c = docker.from_env()
        c.ping()
        return c
    except Exception:
        return None


@contextlib.contextmanager
def provision(pull_if_missing: bool = True):
    """Context manager yielding a fresh Sandbox, destroyed on exit.

    Usage:
        with provision() as sb:
            if sb.available:
                code, out = sb.exec(["python", "-c", "print(1+1)"])
    """
    client = _client()
    if client is None:
        # Graceful degradation: no daemon here (e.g. CI without Docker).
        # S1 still works because parsing happens on the host.
        yield Sandbox(container=None, image=BASE_IMAGE, available=False)
        return

    if pull_if_missing:
        with contextlib.suppress(Exception):
            client.images.get(BASE_IMAGE)  # raises if missing -> pull
    container = client.containers.run(BASE_IMAGE, **SANDBOX_KWARGS)
    sb = Sandbox(container=container, image=BASE_IMAGE, available=True)
    try:
        yield sb
    finally:
        sb.destroy()


def provision_probe() -> dict:
    """Cheap health check you can call in Week 1 to confirm S0 works.

    Returns a dict describing whether Docker is reachable and a container can
    be created + exec'd + destroyed. This is the 5-minute sanity check.
    """
    started = time.time()
    with provision() as sb:
        if not sb.available:
            return {"docker": False, "note": "no daemon reachable (host-only mode)"}
        code, out = sb.exec(["python", "-c", "print('sandbox-ok')"])
        return {
            "docker": True,
            "image": sb.image,
            "exec_exit_code": code,
            "exec_output": out.strip(),
            "elapsed_s": round(time.time() - started, 2),
        }
