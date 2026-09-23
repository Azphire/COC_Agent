"""Optional sourced launch constraints; old preparations retain editable defaults."""

import json
from pathlib import Path


def preparation_requirements(document):
    raw = document.get("launch_requirements") or {}
    raw = raw if isinstance(raw, dict) else {}
    source = str(raw.get("source", "")).strip()
    minimum, maximum = raw.get("minimum_players", 1), raw.get("maximum_players", 6)
    era = raw.get("era", "1920s")
    player_values_valid = (
        type(minimum) is int and type(maximum) is int and 1 <= minimum <= maximum <= 12
    )
    if not player_values_valid:
        minimum, maximum = 1, 6
    era_valid = era in {"1920s", "modern"}
    if not era_valid:
        era = "1920s"
    verified_fields = [
        field for field in ("minimum_players", "maximum_players", "era")
        if source and field in raw and (era_valid if field == "era" else player_values_valid)
    ]
    return {
        "minimum_players": minimum, "maximum_players": maximum, "era": era,
        "source": source or "未配置来源；可调整默认值",
        "verified_fields": verified_fields,
        "players_verified": {"minimum_players", "maximum_players"} <= set(verified_fields),
        "era_verified": "era" in verified_fields,
        "verified": bool(verified_fields), "defaults_adjustable": len(verified_fields) < 3,
    }


async def resolved_requirements(session, prep):
    from app.persistence.launch_models import LaunchDefault

    saved = await session.get(
        LaunchDefault, f"preparation:{prep.id}:{prep.version}:{prep.source_hash}",
    )
    document = {**prep.document, **(saved.document if saved else {})}
    if not saved and "launch_requirements" not in prep.document:
        path = Path(__file__).resolve().parents[1] / "launch" / "requirements.json"
        reviewed = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        if prep.source_hash in reviewed:
            document["launch_requirements"] = reviewed[prep.source_hash]
    return preparation_requirements(document)
