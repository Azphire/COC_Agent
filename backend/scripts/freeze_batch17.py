"""Freeze exact application, test and package bytes before a named acceptance attempt."""

import hashlib
import subprocess
import sys
from datetime import datetime, timezone

from module_package import ROOT, write


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def freeze(name):
    directory = (ROOT / "data/prepared/changan/batch-17" / name).resolve()
    assert directory.is_relative_to(ROOT / "data/prepared/changan/batch-17")
    directory.mkdir(parents=True, exist_ok=True)
    assert not (directory / "freeze.json").exists(), "Never replace a frozen acceptance attempt"
    files = sorted(
        {
            p
            for base in ("backend/app", "backend/tests", "backend/scripts", "frontend/src")
            for p in (ROOT / base).rglob("*")
            if p.suffix in {".py", ".ts", ".tsx", ".css"}
        }
    )
    code = {p.relative_to(ROOT).as_posix(): sha(p) for p in files}
    code_digest = hashlib.sha256(
        "\n".join(f"{path} {digest}" for path, digest in code.items()).encode()
    ).hexdigest()
    packages = {}
    for name in (
        "package-original.json",
        "package-supplement-test.json",
        "npc-supplement-proposal.json",
    ):
        source = ROOT / "data/prepared/changan/batch-17" / name
        (directory / name).write_bytes(source.read_bytes())
        packages[name] = sha(source)
    manifest = {
        "frozen_at_utc": datetime.now(timezone.utc).isoformat(),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "code_sha256": code_digest,
        "code_files": code,
        "packages": packages,
        "model": "qwen3:8b",
        "context_limit": 8192,
        "output_limit": 900,
        "numeric_supplement_user_approved": False,
    }
    write(directory / "freeze.json", manifest)
    print(directory, code_digest)


if __name__ == "__main__":
    freeze(sys.argv[1])
