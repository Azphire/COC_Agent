"""Normal API equipment smoke in the ORIGINAL isolated new-card room.

The explicitly authored training NPC is a fixture, not Changan or NPC v1.
Attack dice are actual server dice; no retry for a different result.
"""

import sys
import time
from uuid import uuid4

import check_batch13 as driver
from module_package import ROOT, read, write


def main():
    directory = ROOT / "data/prepared/changan/batch-21" / sys.argv[1]
    assert directory.name.startswith("new-card-")
    driver.DIRECTORY = directory
    driver.BASE = "http://127.0.0.1:8022/api"
    driver.HOST = "local-package-review"
    request = driver.request
    session = read(directory / "session.json")
    prefix = session["prefix"]
    room = request("GET", prefix)
    if len(sys.argv) > 2 and sys.argv[2] == "finish":
        evidence = read(directory / "equipment-smoke.json")
        for _ in range(120):
            room = request("GET", prefix)
            pending = room["combat"].get("pending")
            cycle = room["game"].get("cycle") or {}
            if pending and pending.get("participant_id") == session["player_id"]:
                stage = pending["stage"]
                operation = (
                    "dodge"
                    if stage == "defense"
                    else "accept"
                    if stage.endswith("choice")
                    else "roll"
                )
                body = dict(action_id=pending["id"], stage=stage, operation=operation)
                result = request("POST", prefix + "/combat/step", body, player=True)
                evidence["calls"].append(dict(path="/combat/step", body=body, result=result))
                write(directory / "equipment-smoke.json", evidence)
            elif not pending and cycle.get("status") in {None, "completed", "cancelled"}:
                body = dict(
                    operation="end",
                    expected_revision=room["revision"],
                    reason="单次装备隔离练习及回应已完成",
                )
                result = request("POST", prefix + "/combat/control", body)
                evidence["calls"].append(dict(path="/combat/control", body=body, result=result))
                evidence["after_end"] = result["room"]
                write(directory / "equipment-smoke.json", evidence)
                print("Training response settled; combat ended normally")
                return
            time.sleep(1)
        raise TimeoutError("Preserved unfinished training response")
    weapon = next(
        w
        for c in room["session_state"]["characters"].values()
        for w in c["weapons"]
        if w["name"] == "小刀"
    )
    iid = weapon["id"]
    assert any(
        i["instance_id"] == iid and i["holder_id"] == session["player_id"]
        for i in room["inventory"]
    )
    evidence = {
        "kind": "normal_api_with_authored_training_npc",
        "initial_weapon": weapon,
        "start_revision": room["revision"],
        "calls": [],
    }

    def call(path, body, player=False):
        try:
            result = request("POST", prefix + path, body, player=player)
        except Exception as error:
            evidence["error"] = (
                str(getattr(error, "response", None).text)
                if getattr(error, "response", None) is not None
                else str(error)
            )
            write(directory / "equipment-smoke.json", evidence)
            raise
        evidence["calls"].append(dict(path=path, body=body, result=result))
        write(directory / "equipment-smoke.json", evidence)
        return result

    runtime = next(iter(room["session_state"]["characters"].values()))
    call(
        "/combat/setup",
        dict(
            expected_revision=room["revision"],
            member_id=session["player_id"],
            weapons=runtime["weapons"],
            armor=runtime["armor"],
            stats_public=True,
            reason="沿用角色已结算装备进入隔离练习",
        ),
    )
    room = request("GET", prefix)
    call(
        "/combat/setup",
        {
            "expected_revision": room["revision"],
            "reason": "第二十一批原创训练假人，仅验证原装备接入既有战斗服务",
            "npc": dict(
                id="batch21-training",
                npc_id="batch21-training",
                label="训练假人",
                team="training",
                scene_id="square",
                attributes={"dex": 1, "con": 50},
                skills={"brawl": 0, "dodge": 0},
                hp=12,
                hp_max=12,
                source="本脚本明确创作的隔离夹具；不属于常暗原稿或NPC v1",
                stats_public=True,
            ),
        },
    )
    room = request("GET", prefix)
    call(
        "/combat/action",
        dict(
            actor_id=session["player_id"],
            target_id="batch21-training",
            weapon_id=iid,
            operation="attack",
            reason="使用角色原有小刀练习一次攻击",
            client_request_id=str(uuid4()),
            turn_key=room["combat"]["turn_key"],
        ),
        player=True,
    )
    for _ in range(180):
        room = request("GET", prefix)
        pending = room["combat"].get("pending")
        if not pending:
            break
        if pending["stage"] == "attack_roll":
            call(
                "/combat/step",
                dict(action_id=pending["id"], stage=pending["stage"], operation="roll"),
                player=True,
            )
        elif pending["stage"] == "attack_choice":
            call(
                "/combat/step",
                dict(action_id=pending["id"], stage=pending["stage"], operation="accept"),
                player=True,
            )
        else:
            time.sleep(1)
    else:
        raise TimeoutError("Preserved unfinished equipment combat")
    evidence["final"] = room
    actions = room["session_state"]["combat"]["actions"].values()
    assert any(
        a.get("weapon", {}).get("id") == iid
        and a.get("stage") == "done"
        and a.get("rolls", {}).get("attack")
        for a in actions
    )
    evidence["status"] = "passed"
    write(directory / "equipment-smoke.json", evidence)
    print("Original knife instance used through combat API:", iid)


if __name__ == "__main__":
    main()
