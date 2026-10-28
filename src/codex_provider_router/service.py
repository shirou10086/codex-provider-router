from __future__ import annotations

import os
from pathlib import Path
import plistlib
import subprocess
import sys

from .config import RouterConfig


LABEL = "com.codex-provider-router"


class LaunchAgentService:
    def __init__(self, config: RouterConfig) -> None:
        self.config = config
        self.plist_path = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    @property
    def supported(self) -> bool:
        return sys.platform == "darwin"

    def install(self) -> None:
        if not self.supported:
            raise ValueError("LaunchAgent installation is only supported on macOS")
        self.plist_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        payload = launch_agent_payload(self.config)
        self.plist_path.write_bytes(plistlib.dumps(payload, sort_keys=False))
        self.plist_path.chmod(0o600)
        self._launchctl("bootout", self._domain_target(), check=False)
        self._launchctl("bootstrap", self._domain_target(), str(self.plist_path))
        self._launchctl("kickstart", "-k", f"{self._domain_target()}/{LABEL}")

    def stop(self) -> None:
        if self.supported:
            self._launchctl("bootout", self._domain_target(), check=False)

    def remove(self) -> None:
        self.stop()
        try:
            self.plist_path.unlink()
        except FileNotFoundError:
            pass

    def status(self) -> dict[str, object]:
        if not self.supported:
            return {"supported": False, "loaded": False, "plist": str(self.plist_path)}
        result = subprocess.run(
            ["launchctl", "print", f"{self._domain_target()}/{LABEL}"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        state: dict[str, object] = {
            "supported": True,
            "loaded": result.returncode == 0,
            "plist": str(self.plist_path),
        }
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                stripped = line.strip()
                if stripped.startswith("state = "):
                    state["state"] = stripped.partition("=")[2].strip()
                elif stripped.startswith("pid = "):
                    state["pid"] = int(stripped.partition("=")[2].strip())
        return state

    def _domain_target(self) -> str:
        return f"gui/{os.getuid()}"

    @staticmethod
    def _launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["launchctl", *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=check,
        )


def _service_path() -> str:
    values = [
        str(Path(sys.executable).resolve().parent),
        "/opt/homebrew/bin",
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ]
    return ":".join(dict.fromkeys(values))


def launch_agent_payload(config: RouterConfig) -> dict[str, object]:
    return {
        "Label": LABEL,
        "ProgramArguments": [
            sys.executable,
            "-m",
            "codex_provider_router",
            "serve",
        ],
        "RunAtLoad": True,
        "KeepAlive": True,
        "ThrottleInterval": 3,
        "ProcessType": "Background",
        "EnvironmentVariables": {
            "HOME": str(Path.home()),
            "CODEX_HOME": str(config.codex_home),
            "PATH": _service_path(),
        },
        "StandardOutPath": str(config.router_home / "router.log"),
        "StandardErrorPath": str(config.router_home / "router.log"),
    }
