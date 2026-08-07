"""SSH tunnel helpers for staging/prod bastions."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Any

import config


def _pid_file(env_label: str) -> Path:
    return config.LOG_DIR / f"tunnel-{env_label.lower()}.pid"


def _log_file(env_label: str) -> Path:
    return config.LOG_DIR / f"tunnel-{env_label.lower()}.log"


def status_for(cfg: dict[str, Any]) -> dict[str, Any]:
    pid_path = _pid_file(cfg["env_label"])
    if not pid_path.exists():
        return {"running": False, "env_label": cfg["env_label"]}
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, 0)
        return {"running": True, "pid": pid, "env_label": cfg["env_label"], "local_port": cfg["local_port"]}
    except (OSError, ValueError):
        return {"running": False, "env_label": cfg["env_label"], "stale_pid_file": True}


def start(cfg: dict[str, Any], force: bool = False) -> dict[str, Any]:
    current = status_for(cfg)
    if current.get("running") and not force:
        return {"ok": True, **current}
    if current.get("running") and force:
        stop(cfg)
    if not cfg.get("ssh_host"):
        return {"ok": False, "error": f"{cfg['env_label']} SSH host not configured", "env_label": cfg["env_label"]}
    key = config.SSH_KEY
    if not Path(key).exists():
        return {"ok": False, "error": f"SSH key missing: {key}", "env_label": cfg["env_label"]}

    cmd = [
        "ssh",
        "-i", key,
        "-N",
        "-L", f"{cfg['local_port']}:{cfg['remote_host']}:{cfg['remote_port']}",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "StrictHostKeyChecking=accept-new",
        f"{config.SSH_USER}@{cfg['ssh_host']}",
    ]
    log_path = _log_file(cfg["env_label"])
    with log_path.open("a", encoding="utf-8") as logf:
        proc = subprocess.Popen(cmd, stdout=logf, stderr=logf)
    _pid_file(cfg["env_label"]).write_text(str(proc.pid))
    time.sleep(1.5)
    if proc.poll() is not None:
        return {
            "ok": False,
            "error": "Tunnel exited early — check logs",
            "log": str(log_path),
            "env_label": cfg["env_label"],
        }
    return {"ok": True, "running": True, "pid": proc.pid, "env_label": cfg["env_label"]}


def stop(cfg: dict[str, Any]) -> dict[str, Any]:
    pid_path = _pid_file(cfg["env_label"])
    if not pid_path.exists():
        return {"ok": True, "stopped": False, "env_label": cfg["env_label"]}
    try:
        pid = int(pid_path.read_text().strip())
        os.kill(pid, signal.SIGTERM)
    except (OSError, ValueError):
        pass
    pid_path.unlink(missing_ok=True)
    return {"ok": True, "stopped": True, "env_label": cfg["env_label"]}


def ensure_running(cfg: dict[str, Any]) -> None:
    st = status_for(cfg)
    if not st.get("running"):
        result = start(cfg)
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "tunnel failed"))
