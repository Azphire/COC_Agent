"""Real startup failure, API without Ollama, Ctrl+C, and port-release checks."""

import ctypes
import json
import os
import signal
import socket
import subprocess
import sys

import httpx
from check_character_creation import ROOT, wait_for


def main():
    directory = ROOT / "data/prepared/changan/batch-22" / sys.argv[1]
    directory.mkdir(parents=True, exist_ok=False)
    root = directory.resolve()
    assert root.is_relative_to((ROOT / "data/prepared/changan/batch-22").resolve())
    kernel = ctypes.windll.kernel32
    if not kernel.GetConsoleWindow():
        kernel.AllocConsole()
        ctypes.windll.user32.ShowWindow(kernel.GetConsoleWindow(), 0)
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    base = "http://127.0.0.1:5174"
    result = {}
    env = {
        **os.environ,
        "APP_PORT": "8025",
        "APP_HOST": "127.0.0.1",
        "DATA_DIR": str(directory),
        "MODEL_SETTINGS_PATH": str(directory / "host-model-settings.json"),
        "DATABASE_URL": "sqlite+aiosqlite:///" + (directory / "game.db").as_posix(),
        "CHECKPOINT_DB_PATH": str(directory / "checkpoint.db"),
        "KNOWLEDGE_DB_PATH": str(directory / "knowledge.db"),
        "HOST_ADMIN_TOKEN": "launcher-check-host",
        "MODEL_PROVIDER": "openai",
        "MODEL_BASE_URL": "https://example.test/v1/",
        "MODEL_NAME": "unconfigured-api",
        "MODEL_API_KEY": "",
        "PYTHONUTF8": "1",
    }
    process = None
    log = None

    def start(name, environment):
        nonlocal process, log
        log = (directory / (name + ".log")).open("w", encoding="utf-8")
        startup = subprocess.STARTUPINFO()
        startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup.wShowWindow = subprocess.SW_HIDE
        process = subprocess.Popen(
            ["cmd.exe", "/c", str(ROOT / "start.cmd"), "--frontend-port", "5174"],
            cwd=directory,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            startupinfo=startup,
        )

    def released():
        for port in (8025, 5174):
            with socket.socket() as sock:
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    return False
        return True

    try:
        assert released(), "Test ports already occupied"
        for name in ("api-start", "api-restart"):
            start(name, env)
            with httpx.Client(
                trust_env=False, timeout=2, headers={"Authorization": "Bearer launcher-check-host"}
            ) as client:
                wait_for(lambda: client.get(base + "/api/health").status_code == 200, 50)
                status = client.get(base + "/api/model/status").json()
                assert status["provider"] == "openai" and status["state"] == "unconfigured"
                # Windows CTRL_C_EVENT (0), on this private console only.
                assert kernel.GenerateConsoleCtrlEvent(0, 0)
                process.wait(timeout=25)
                wait_for(released, 15)
                result[name] = {
                    "ready": True,
                    "unconfigured_api": True,
                    "ctrl_c": True,
                    "exit_code": process.returncode,
                    "ports_released": True,
                }
                process = None
                log.close()
        # Backend initialization fails after both service helpers have been created.
        invalid = directory / "invalid.db"
        invalid.mkdir()
        start(
            "partial-failure", {**env, "DATABASE_URL": "sqlite+aiosqlite:///" + invalid.as_posix()}
        )
        process.wait(timeout=55)
        assert process.returncode != 0
        wait_for(released, 15)
        result["partial_failure"] = {"nonzero_exit": True, "ports_released": True}
        process = None
        log.close()
        # An occupied port is reported without touching its existing owner.
        with socket.socket() as existing:
            existing.bind(("127.0.0.1", 8025))
            existing.listen()
            start("occupied-port", env)
            process.wait(timeout=20)
            assert process.returncode != 0
            with socket.socket() as probe:
                assert probe.connect_ex(("127.0.0.1", 8025)) == 0
            result["occupied_port"] = {"existing_owner_preserved": True}
            process = None
            log.close()
        result["status"] = "passed"
        print(json.dumps(result, indent=2), flush=True)
    finally:
        if process and process.poll() is None:
            kernel.GenerateConsoleCtrlEvent(0, 0)
            process.wait(timeout=25)
        if log:
            log.close()
        (directory / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
