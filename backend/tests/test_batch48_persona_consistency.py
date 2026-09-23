"""Original live low-skill failure and opposite examples, without model review calls."""

import copy
import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from test_batch48_party import create, next_member, party  # noqa: F401

from app.agents.schemas import ProfileInput
from app.party.persona import ability_issues, enforce_public_identity, unique_name


def test_retained_live_tracking_claim_is_rejected(rulesets):
    # Retained live run: real-01/after-persona-retry.json, first rerolled member.
    original = "他擅长追踪与机械维修，尤其对极地环境有丰富经验。"
    card = SimpleNamespace(skill_values={"track": 15, "mechanical_repair": 79})
    issues = ability_issues({"background": original}, card, rulesets["coc7-character-creation"])
    assert [issue["skill"] for issue in issues] == ["track"]
    assert issues[0]["value"] == 15
    assert issues[0]["claim"] == "他擅长追踪与机械维修"


def test_retained_live_university_qualification_is_bound_to_actual_skills(rulesets):
    # Retained live source: real-01/host/persona-consistency-retry-result.json.
    original = "曾在大学教授历史与人类学，后因一场意外放弃教职。"
    card = SimpleNamespace(skill_values={"history": 20, "anthropology": 1})
    issues = ability_issues({"background": original}, card, rulesets["coc7-character-creation"])
    assert {issue["skill"] for issue in issues} == {"history", "anthropology"}
    assert {issue["value"] for issue in issues} == {1, 20}


@pytest.mark.parametrize("text", [
    "他曾当过仓库工人，后来改行做猎人。",
    "他在大学旁听历史与人类学课程。",
    "他曾在中学教授历史基础课。",
    "他的导师曾在大学教授历史，他负责机械维修。",
    "他从未在大学教授历史。",
    "他计划在大学教授历史，但目前仍在学习。",
    "他曾在大学教授机械维修。",
])
def test_career_change_learning_other_teachers_and_high_skills_remain_valid(rulesets, text):
    card = SimpleNamespace(skill_values={"history": 20, "anthropology": 1, "mechanical_repair": 79})
    assert not ability_issues({"background": text}, card, rulesets["coc7-character-creation"])


@pytest.mark.parametrize("text", [
    "他并不擅长追踪，但精通机械维修。",
    "他擅长机械维修，追踪只是兴趣。",
    "他希望有朝一日精通追踪。",
    "追踪是他愿意尝试但并不拿手的工作。",
    "他的导师擅长追踪，他负责机械维修。",
    "他并非追踪专家。",
    "精通追踪是他的梦想。",
    "他很熟练地修理机械，但遇到追踪会请教队友。",
    "他擅长机械维修而追踪并不熟练。",
    "他擅长机械维修也不熟悉追踪。",
])
def test_weakness_intention_and_other_person_are_not_blocked(rulesets, text):
    card = SimpleNamespace(skill_values={"track": 15, "mechanical_repair": 79})
    assert not ability_issues({"background": text}, card, rulesets["coc7-character-creation"])


@pytest.mark.parametrize("proposed,existing", [
    ("林修远", "林修远"), ("林修远·猎人", "林修远"), ("林修远", "林修远·猎人"),
])
def test_only_current_same_name_member_gets_disambiguated(proposed, existing):
    other = {"index": 1, "character": {"name": existing, "occupation": "librarian"},
             "profile": {"name": existing, "background": "原始人物文字，不能因别人重抽而改变。"}}
    member = {"index": 0,
              "stream_seed": "b78b447cf8dceac6d7da88891f5086943e71837b17a36cc66289f2d71e515d4e",
              "character": {"name": "旧名", "occupation": "big_game_hunter"}}
    document = {"members": [member, other]}
    original_other = json.dumps(other, ensure_ascii=False)
    original_document = copy.deepcopy(document)
    profile = ProfileInput(role="investigator", name=proposed)
    unique_name(profile, member, document)
    assert profile.name not in existing and existing not in profile.name
    assert "·" not in profile.name
    assert member["original_name"] == proposed
    assert json.dumps(other, ensure_ascii=False) == original_other
    repeated = ProfileInput(role="investigator", name=proposed)
    unique_name(repeated, original_document["members"][0], original_document)
    assert repeated.name == profile.name


@pytest.mark.parametrize("collision", ["exact", "longer", "shorter"])
def test_ho_public_name_avoids_substrings_without_private_text_or_other_changes(collision):
    member = {"index": 0, "stream_seed": "private-name-collision", "character": {
        "name": "待生成", "module_handout": {"text": "PRIVATE-HO-ONE"},
    }, "profile": {"name": "PRIVATE-MODEL-NAME"}}
    probe = copy.deepcopy(member)
    enforce_public_identity(probe, {"members": [probe]})
    name = probe["public_name"]
    other_name = {"exact": name, "longer": name + "·职业", "shorter": name[1:]}[collision]
    other = {"index": 1, "character": {"name": other_name},
             "profile": {"name": other_name, "background": "保留另一人物全部原稿。"}}
    document = {"members": [member, other]}
    untouched = json.dumps(other, ensure_ascii=False)
    repeat = copy.deepcopy(document)
    repeat["members"][0]["character"]["module_handout"]["text"] = "DIFFERENT-PRIVATE-HO"
    enforce_public_identity(member, document)
    enforce_public_identity(repeat["members"][0], repeat)
    assert member["public_name"] == repeat["members"][0]["public_name"]
    assert member["public_name"] not in other_name and other_name not in member["public_name"]
    assert member["profile"]["name"] == member["character"]["name"] == member["public_name"]
    assert member["original_name"] == "PRIVATE-MODEL-NAME"
    assert json.dumps(other, ensure_ascii=False) == untouched


def test_reroll_name_collision_keeps_other_member_and_adopted_identity(client, party):  # noqa: F811
    svc, model = party
    batch = create(client)
    prefix = f"/api/party-batches/{batch['id']}"
    model.forced_name = "张雨田"
    next_member(client, batch["id"])
    model.forced_name = "林修远"
    next_member(client, batch["id"])
    original = client.portal.call(svc.get, batch["id"])
    other = json.dumps(original["members"][1], ensure_ascii=False)
    assert client.post(prefix + "/reroll", json={
        "request_id": str(uuid4()), "member_index": 0,
    }).status_code == 200
    ready = next_member(client, batch["id"])
    assert ready["status"] == "ready"
    stored = client.portal.call(svc.get, batch["id"])
    first_name = stored["members"][0]["profile"]["name"]
    assert first_name not in "林修远" and "林修远" not in first_name
    assert stored["members"][0]["original_name"] == "林修远"
    assert json.dumps(stored["members"][1], ensure_ascii=False) == other
    assert len(model.messages) == 3  # No extra generation to resolve a name.

    # An old suffix name is a valid already-adopted identity. Reconstruct that
    # earlier stored result in this isolated fixture; review must not migrate it.
    async def retain_old_name():
        document = await svc.get(batch["id"])
        first = document["members"][0]
        first["character"]["name"] = first["profile"]["name"] = "林修远·猎人"
        await svc._save(document)

    client.portal.call(retain_old_name)
    adopted = client.post(prefix + "/adopt", json={"request_id": str(uuid4())})
    assert adopted.status_code == 200, adopted.text
    original_adopted = client.portal.call(svc.get, batch["id"])
    reviewed = client.portal.call(svc.review, batch["id"])
    assert json.dumps(reviewed, ensure_ascii=False) == json.dumps(
        original_adopted, ensure_ascii=False,
    )
    card = client.get(f"/api/characters/{reviewed['character_ids'][0]}").json()
    assert card["name"] == "林修远·猎人"


def low_skill(svc, member):
    skills = member["character"]["skill_values"]
    definitions = svc.characters.rulesets[member["character"]["ruleset_id"]].skills
    return next(s for s in definitions if s.allocatable and 0 < skills[s.key] <= 30
                and not s.specialization_group)


def test_new_failure_keeps_original_and_retries_only_text(client, party):  # noqa: F811
    svc, model = party
    batch = create(client, count=1)
    original = copy.deepcopy(batch["members"][0]["character"])
    skill = low_skill(svc, batch["members"][0])
    model.background_override = f"他擅长{skill.display_name}。"
    failed = next_member(client, batch["id"])
    assert failed["status"] == "failed" and failed["members"][0]["model_calls"] == 1
    assert failed["members"][0]["character"] == original
    stored = client.portal.call(svc.get, batch["id"])
    rejection = stored["members"][0]["_persona_rejections"][0]
    assert rejection["profile"]["background"] == model.background_override
    assert rejection["issues"][0]["skill"] == skill.key
    assert "raw_error" not in json.dumps(failed)
    assert "_persona_rejections" not in json.dumps(failed)
    model.background_override = f"他并不擅长{skill.display_name}，愿意向队友请教。"
    ready = next_member(client, batch["id"])
    assert ready["status"] == "ready" and len(model.messages) == 2
    assert ready["members"][0]["character"]["roll_records"] == original["roll_records"]
    retry_context = json.loads(model.messages[-1][-1]["content"])
    assert retry_context["previous_output_issues"]


@pytest.mark.parametrize("entry", ["review", "adopt"])
def test_old_ready_output_is_persistently_rejected_without_a_call(client, party, entry):  # noqa: F811
    svc, model = party
    batch = create(client, count=1)
    ready = next_member(client, batch["id"])
    skill = low_skill(svc, ready["members"][0])

    async def old_output():
        document = await svc.get(batch["id"])
        member = document["members"][0]
        member["profile"]["background"] = f"他擅长{skill.display_name}。"
        await svc._save(document)
        return copy.deepcopy(member["character"])

    original_card = client.portal.call(old_output)
    if entry == "review":
        client.portal.call(svc.review, batch["id"])
    else:
        result = client.post(f"/api/party-batches/{batch['id']}/adopt", json={
            "request_id": str(uuid4()),
        })
        assert result.status_code == 422
    stored = client.portal.call(svc.get, batch["id"])
    assert stored["status"] == "failed"
    assert stored["members"][0]["character"] == original_card
    assert stored["members"][0]["profile"]["background"] == f"他擅长{skill.display_name}。"
    assert len(stored["members"][0]["_persona_rejections"]) == 1
    assert len(model.messages) == 1
    # Reviewing the same failed output again neither loses it nor appends duplicate records.
    client.portal.call(svc.review, batch["id"])
    repeated = client.portal.call(svc.get, batch["id"])
    assert len(repeated["members"][0]["_persona_rejections"]) == 1
