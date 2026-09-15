"""Windows launcher. A private Job Object owns only the services started here."""

import argparse
import ctypes
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))


class WindowsJob:
    def __init__(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.c_void_p,
            wintypes.DWORD,
        ]
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]

        class Basic(ctypes.Structure):
            _fields_ = [
                ("process_time", ctypes.c_int64),
                ("job_time", ctypes.c_int64),
                ("flags", wintypes.DWORD),
                ("min_ws", ctypes.c_size_t),
                ("max_ws", ctypes.c_size_t),
                ("active", wintypes.DWORD),
                ("affinity", ctypes.c_size_t),
                ("priority", wintypes.DWORD),
                ("scheduling", wintypes.DWORD),
            ]

        class Extended(ctypes.Structure):
            _fields_ = [
                ("basic", Basic),
                ("io", ctypes.c_uint64 * 6),
                ("process_memory", ctypes.c_size_t),
                ("job_memory", ctypes.c_size_t),
                ("peak_process", ctypes.c_size_t),
                ("peak_job", ctypes.c_size_t),
            ]

        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        info = Extended()
        info.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel.SetInformationJobObject(
            self.handle, 9, ctypes.byref(info), ctypes.sizeof(info)
        ):
            self.close()
            raise ctypes.WinError(ctypes.get_last_error())

    def assign(self, process):
        if not self.kernel.AssignProcessToJobObject(self.handle, int(process._handle)):
            process.kill()
            process.wait()
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def service_child(kind):
    # No descendant exists until the launcher assigns this helper to its job.
    # A dead parent closes the pipe, so an unassigned helper exits harmlessly.
    if sys.stdin.buffer.read(1) != b"G":
        return 1
    command = (
        [sys.executable, "-X", "utf8", "-m", "app.main"]
        if kind == "backend"
        else [shutil.which("node"), str(ROOT / "frontend/node_modules/vite/bin/vite.js")]
    )
    process = subprocess.Popen(command, cwd=BACKEND if kind == "backend" else ROOT / "frontend")
    try:
        return process.wait()
    except KeyboardInterrupt:
        try:
            return process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            return 1


def available_port(host, port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            sock.bind((host, port))
        except OSError:
            raise RuntimeError(
                f"Port {port} is occupied; stop its owner or change the port."
            ) from None


def preflight():
    for executable in ("uv", "node", "npm.cmd"):
        if not shutil.which(executable):
            raise RuntimeError(f"Missing {executable}. Install it, then restart start.cmd.")
    version = subprocess.check_output([shutil.which("node"), "--version"], text=True).strip()
    if tuple(int(v) for v in version.lstrip("v").split(".")[:2]) < (22, 12):
        raise RuntimeError("Node >=22.12 required.")
    try:
        import fastapi  # noqa: F401
        import httpx  # noqa: F401
        import langgraph  # noqa: F401
        import openai  # noqa: F401
        import sqlalchemy  # noqa: F401
        import uvicorn  # noqa: F401
    except ImportError:
        raise RuntimeError(
            f'Install dependencies: uv sync --directory "{BACKEND}" --locked'
        ) from None
    frontend = ROOT / "frontend"
    for name in ("vite", "react", "react-dom", "@vitejs/plugin-react", "typescript", "oxlint"):
        if not (frontend / "node_modules" / name / "package.json").exists():
            raise RuntimeError(f'Install dependencies: npm.cmd --prefix "{frontend}" ci')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lan", action="store_true", help="Allow LAN players via frontend")
    parser.add_argument("--frontend-port", type=int, default=5173)
    parser.add_argument("--service", choices=["backend", "frontend"], help=argparse.SUPPRESS)
    args = parser.parse_args()
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, signal.default_int_handler)
    if args.service:
        return service_child(args.service)
    if os.name != "nt":
        print("This launcher requires Windows.")
        return 1
    # Also accept Ctrl+C when invoked from a shell-created process group.
    ctypes.windll.kernel32.SetConsoleCtrlHandler(None, False)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    processes, job = [], None
    try:
        preflight()
        from app.config import Settings
        from app.models.settings import ModelSettings

        settings = Settings()
        model = ModelSettings(settings)
        if not settings.host_admin_token.get_secret_value():
            raise RuntimeError(
                f'Host key missing. Run: uv run --directory "{BACKEND}" python scripts/host_key.py'
            )
        if not 1 <= args.frontend_port <= 65535 or args.frontend_port == settings.app_port:
            raise RuntimeError("Frontend port must be 1-65535 and differ from APP_PORT.")
        lan = args.lan or settings.app_host == "0.0.0.0"
        frontend_host = "0.0.0.0" if lan else "127.0.0.1"
        available_port(settings.app_host, settings.app_port)
        available_port(frontend_host, args.frontend_port)
        print(
            f"Model: {model.current.provider} / {model.current.model or '(not configured)'}",
            flush=True,
        )
        if model.current.provider == "ollama":
            import httpx

            try:
                response = httpx.get("http://127.0.0.1:11434/api/tags", timeout=2, trust_env=False)
                response.raise_for_status()
                names = [entry["name"] for entry in response.json().get("models", [])]
                expected = settings.model_name
                if ":" not in expected:
                    expected += ":latest"
                if expected not in names:
                    print(
                        "Selected Ollama model is missing. Model settings and cards remain usable."
                    )
            except (httpx.HTTPError, ValueError, KeyError):
                print(
                    "Ollama unavailable. Open Model settings to select an API; cards remain usable."
                )
        elif not model.ready():
            print("API is not configured. Open Model settings; cards remain usable.")
        env = {
            **os.environ,
            "PYTHONUTF8": "1",
            "PYTHONUNBUFFERED": "1",
            "COC_BACKEND_PORT": str(settings.app_port),
            "COC_BACKEND_HOST": "127.0.0.1"
            if settings.app_host == "0.0.0.0"
            else settings.app_host,
            "COC_FRONTEND_PORT": str(args.frontend_port),
            "COC_FRONTEND_HOST": frontend_host,
        }
        job = WindowsJob()
        for kind in ("backend", "frontend"):
            child = subprocess.Popen(
                [sys.executable, "-X", "utf8", str(Path(__file__).resolve()), "--service", kind],
                cwd=ROOT,
                env=env,
                stdin=subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
            processes.append(child)
            job.assign(child)
            child.stdin.write(b"G")
            child.stdin.flush()
            child.stdin.close()
        import httpx

        deadline = time.monotonic() + 45
        address = f"http://127.0.0.1:{args.frontend_port}"
        with httpx.Client(timeout=1, trust_env=False) as client:
            while True:
                if any(p.poll() is not None for p in processes):
                    raise RuntimeError("A service exited during startup; see the error above.")
                try:
                    health = client.get(address + "/api/health")
                    if (
                        client.get(address).status_code == 200
                        and health.json().get("status") == "ok"
                    ):
                        break
                except (httpx.HTTPError, ValueError):
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("Services did not become ready within 45 seconds.")
                time.sleep(0.3)
        print(f"\nReady: {address}\nModel settings: {address}/#/status", flush=True)
        if lan:
            addresses = sorted(
                {
                    ip
                    for ip in socket.gethostbyname_ex(socket.gethostname())[2]
                    if not ip.startswith("127.")
                }
            )
            for ip in addresses:
                print(f"Player join: http://{ip}:{args.frontend_port}/#/rooms", flush=True)
            print("Players use the room invite code; keep the host key private.", flush=True)
        print("Ctrl+C stops only the services started by this launcher.", flush=True)
        while all(p.poll() is None for p in processes):
            time.sleep(0.3)
        raise RuntimeError("A service exited; stopping this launcher session.")
    except KeyboardInterrupt:
        print("\nStopping services...", flush=True)
        return 0
    except Exception as error:
        # Settings validation may include secrets; do not print its input values.
        print(
            str(error)
            if isinstance(error, RuntimeError)
            else f"Launcher failed ({type(error).__name__}); check configuration and dependencies.",
            flush=True,
        )
        return 1
    finally:
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if hasattr(signal, "SIGBREAK"):
            signal.signal(signal.SIGBREAK, signal.SIG_IGN)
        for process in processes:
            if process.poll() is None:
                try:
                    process.send_signal(signal.CTRL_BREAK_EVENT)
                except OSError:
                    pass
        deadline = time.monotonic() + 8
        while any(p.poll() is None for p in processes) and time.monotonic() < deadline:
            time.sleep(0.1)
        if job:
            job.close()  # Kills remaining descendants even if a helper already exited.
        for process in processes:
            process.wait(timeout=5)
        if processes:
            print("Launcher services stopped.", flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
