import json
import subprocess

import pytest

from scripts import tailscale_access as ts


def test_cli_missing(monkeypatch):
    monkeypatch.setattr(ts, "locate_cli", lambda: None)
    result = ts.detect_tailscale()
    assert result.ipv4 is None and "安装" in result.message
    assert "尚未就绪" in result.entry(5187)


@pytest.mark.parametrize(
    "state,online,ip,expected",
    [
        ("NeedsLogin", False, "", "登录"),
        ("Stopped", False, "", "连接"),
        ("NeedsMachineAuth", False, "", "批准"),
        ("Running", False, "", "离线"),
        ("Running", True, "100.64.1.9", "http://100.64.1.9:5187/#/rooms"),
        ("Running", True, "192.168.1.10", "核对"),
        ("Running", True, "", "核对"),
        ("Running", True, "::1", "核对"),
    ],
)
def test_state_and_actual_self_ip(monkeypatch, state, online, ip, expected):
    monkeypatch.setattr(ts, "locate_cli", lambda: "tailscale.exe")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        assert kwargs["timeout"] == 3 and kwargs["capture_output"]
        stdout = (
            json.dumps(
                {
                    "BackendState": state,
                    "Self": {"Online": online, "TailscaleIPs": ["100.64.1.9"]},
                    "Peer": {"secret-device": {}},
                    "User": {"secret-account": {}},
                }
            )
            if command[1] == "status"
            else ip + "\n"
        )
        return subprocess.CompletedProcess(command, 0, stdout=stdout)

    monkeypatch.setattr(ts.subprocess, "run", run)
    entry = ts.detect_tailscale().entry(5187)
    assert expected in entry
    assert "secret" not in entry
    assert commands[0] == ["tailscale.exe", "status", "--json"]
    if len(commands) > 1:
        assert commands[1] == ["tailscale.exe", "ip", "-4"]


@pytest.mark.parametrize(
    "error,expected",
    [
        (subprocess.TimeoutExpired("tailscale", 3), "超时"),
        (subprocess.CalledProcessError(1, "tailscale", stderr="SECRET"), "服务"),
        (OSError("SECRET"), "服务"),
    ],
)
def test_cli_failure_is_safe_and_nonfatal(monkeypatch, error, expected):
    monkeypatch.setattr(ts, "locate_cli", lambda: "tailscale.exe")

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(ts.subprocess, "run", fail)
    entry = ts.detect_tailscale().entry(5173)
    assert expected in entry and "SECRET" not in entry and "本机车卡" in entry


def test_standard_windows_install_discovery(monkeypatch, tmp_path):
    cli = tmp_path / "Tailscale/tailscale.exe"
    cli.parent.mkdir()
    cli.write_bytes(b"")
    monkeypatch.setattr(ts.shutil, "which", lambda _: None)
    monkeypatch.setenv("ProgramW6432", str(tmp_path))
    assert ts.locate_cli() == str(cli)
