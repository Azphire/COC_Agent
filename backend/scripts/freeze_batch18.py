"""Record reproducible local code, package and model settings without a commit."""

import hashlib
import subprocess
import sys
from datetime import UTC, datetime

from module_package import ROOT, settings_for, write


def freeze(label):
    directory = ROOT / "data/prepared/changan/batch-18"
    target = directory / (label + "-freeze.json")
    if target.exists():
        raise ValueError("Keep each earlier version record")
    files = [
        p
        for folder in ("backend/app", "backend/scripts", "backend/tests", "frontend/src")
        for p in (ROOT / folder).rglob("*")
        if p.is_file() and p.suffix in {".py", ".tsx", ".ts", ".css"}
    ]
    files += [ROOT / "backend/pyproject.toml", directory / "package-approved.json"]
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
    settings = settings_for(directory / "unused-config-only")
    write(
        target,
        {
            "label": label,
            "frozen_at": datetime.now(UTC).isoformat(),
            "head": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "files": hashes,
            "provider": settings.model_provider,
            "model": settings.model_name,
            "context_limit": settings.model_context_limit,
            "output_limit": settings.model_output_limit,
            "keeper_plan_output_limit": min(1600, settings.model_context_limit - 2100),
            "temperature": settings.model_temperature,
            "think": False,
            "keep_alive": "5m",
            "note": (
                "Options are derived from this frozen launch configuration; model-calls.json "
                "records outputs, latency and usage, not a wire capture."
            ),
        },
    )
    print(target)


if __name__ == "__main__":
    freeze(sys.argv[1])
