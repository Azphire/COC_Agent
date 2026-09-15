"""Read only this machine's Tailscale readiness; never print CLI account/peer data."""

import ipaddress
import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class TailscaleAccess:
    ipv4: str | None = None
    message: str = ""

    def entry(self, port):
        if self.ipv4:
            return f"Tailscale 异地加入地址: http://{self.ipv4}:{port}/#/rooms"
        return f"Tailscale 异地入口尚未就绪: {self.message} 本机车卡仍可使用。"


def locate_cli():
    found = shutil.which("tailscale.exe") or shutil.which("tailscale")
    if found:
        return found
    for variable in ("ProgramW6432", "ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(variable)
        if root:
            candidate = Path(root) / "Tailscale/tailscale.exe"
            if candidate.is_file():
                return str(candidate)
    return None


def detect_tailscale():
    cli = locate_cli()
    if not cli:
        return TailscaleAccess(message="未找到 CLI，请安装 Tailscale Windows 客户端并登录。")

    def run(*args):
        result = subprocess.run(
            [cli, *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=3,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return result.stdout

    try:
        status = json.loads(run("status", "--json"))
        if not isinstance(status, dict):
            raise ValueError
        state = status.get("BackendState")
        if state in {"NeedsLogin", "NoState"}:
            return TailscaleAccess(message="尚未登录，请在 Tailscale 客户端完成登录。")
        if state == "NeedsMachineAuth":
            return TailscaleAccess(message="设备等待批准，请联系当前 Tailscale 网络管理员。")
        if state != "Running":
            return TailscaleAccess(message="服务未连接，请打开 Tailscale 客户端并连接网络。")
        own = status.get("Self")
        if not isinstance(own, dict) or own.get("Online") is not True:
            return TailscaleAccess(message="本机离线，请检查 Tailscale 客户端连接状态。")
        address = ipaddress.IPv4Address(run("ip", "-4").strip())
        if str(address) not in own.get("TailscaleIPs", []):
            raise ValueError
        if address.is_loopback or address.is_unspecified or address.is_multicast:
            raise ValueError
        return TailscaleAccess(ipv4=str(address))
    except subprocess.TimeoutExpired:
        return TailscaleAccess(message="CLI 检测超时，请检查本机 Tailscale 服务后重新启动。")
    except (OSError, subprocess.CalledProcessError):
        return TailscaleAccess(message="无法连接 CLI 服务，请检查 Tailscale 是否正在运行并登录。")
    except (ValueError, TypeError):
        return TailscaleAccess(message="无法核对本机 IPv4，请在 Tailscale 客户端检查地址与版本。")
