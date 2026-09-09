"""Initialize the ignored local host key, or explicitly display it with --show."""

import argparse
import re
import secrets
import sys
from io import StringIO
from pathlib import Path

from dotenv import dotenv_values

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import REPO_ROOT, Settings  # noqa: E402


def initialize(path: Path) -> None:
    content = path.read_text(encoding="utf-8") if path.exists() else ""
    if not (dotenv_values(stream=StringIO(content)).get("HOST_ADMIN_TOKEN") or "").strip():
        content = re.sub(r"(?m)^HOST_ADMIN_TOKEN\s*=.*(?:\n|$)", "", content)
        path.write_text(
            content.rstrip("\r\n") + "\nHOST_ADMIN_TOKEN=" + secrets.token_urlsafe(32) + "\n",
            encoding="utf-8",
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--show", action="store_true", help="Explicitly print the host key locally")
    args = parser.parse_args()
    initialize(REPO_ROOT / ".env")
    if args.show:
        print(Settings().host_admin_token.get_secret_value())
    else:
        print("Host key configured in the ignored root .env; value not displayed.")
