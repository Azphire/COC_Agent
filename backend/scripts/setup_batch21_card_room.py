"""Import the browser-created card into an isolated live room using normal APIs."""

import sys
import time

import check_batch13 as driver
from module_package import ROOT, read, write


def main():
    directory = ROOT / "data/prepared/changan/batch-21" / sys.argv[1]
    directory.mkdir(exist_ok=False)
    driver.DIRECTORY = directory
    driver.BASE = "http://127.0.0.1:8022/api"
    driver.HOST = "local-package-review"
    request = driver.request
    card = request(
        "POST", "/characters/import", read(directory.parent / "browser-c2/musician-export.json")
    )
    card = request("POST", f"/characters/{card['id']}/finalize", {"version": card["version"]})
    created = request("POST", "/rooms", {"name": "第二十一批新卡 · 专业与装备短复验"})
    prefix = "/rooms/" + created["room"]["id"]
    joined = request(
        "POST", "/rooms/join", {"invite_code": created["invite_code"], "display_name": "新卡调查员"}
    )
    member = joined["room"]["self_member_id"]
    room = request("POST", prefix + "/character-slots", {"character_id": card["id"]})["room"]
    slot = room["character_slots"][0]["id"]
    request("POST", prefix + "/character-assignments", {"slot_id": slot, "member_id": member})
    request("POST", prefix + "/module", {"module_id": "stopped-clock"})
    profile = request("POST", "/agent-profiles", {"role": "keeper", "name": "KP"})
    request(
        "POST",
        prefix + "/agent-bindings",
        {"member_id": room["host_member_id"], "profile_id": profile["id"]},
    )
    write(
        directory / "session.json",
        {
            "prefix": prefix,
            "token": joined["member_token"],
            "player_id": member,
            "started": time.time(),
        },
    )
    write(directory / "load-info.json", {"server_data": "short-c2", "port": 8022})
    write(
        directory / "scenario.json",
        {
            "module": "仓库原创停摆的钟楼",
            "card_source": "browser-c2/musician-export.json",
            "scope": "new specialty and original equipment live smoke; "
            "not formal module or long test",
            "dice": "actual server random, no reset",
        },
    )
    request("POST", prefix + "/ready", {"ready": True}, player=True)
    room = request("POST", prefix + "/start")["room"]
    write(directory / "initial.json", room)
    assert len(room["inventory"]) == 2
    assert room["character_slots"][0]["character_snapshot"]["skill_values"]["science_physics"] == 41
    assert room["session_state"]["characters"][slot]["equipment_settlement"] == "retained"
    print("New card room initialized; 2 real items, physics 41")


if __name__ == "__main__":
    main()
