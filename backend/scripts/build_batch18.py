"""Derive batch 18 from the byte-verified, approved batch 17 package only."""

import copy
import hashlib
import shutil

from module_package import ROOT, read, write

SOURCE = ROOT / "data/prepared/changan/batch-17/approved-v1"
OUTPUT = ROOT / "data/prepared/changan/batch-18"
EXPECTED = "f885a9313c80dd67141ea1bacf049e0e8ed8d37109349af4f8b6a2f7c58315b8"


def build():
    source = SOURCE / "package-supplement-approved.json"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if digest != EXPECTED:
        raise ValueError("Approved source changed; inspect its provenance before deriving")
    package = read(source)
    original = copy.deepcopy(package)
    entities = {e["key"]: e for e in package["entities"]}
    fields = {k: e["fields"] for k, e in entities.items()}
    alias_map = {
        "keys": ["钥匙", "两把钥匙", "黑包", "黑色包", "那个包"],
        "phone": ["手机"],
        "torch": ["手电", "手电筒"],
        "clicker": ["怪物", "非人形态", "喘息来源", "声音来源"],
        "quiet_passage": ["通道", "前门"],
        "panel": ["面板"],
        "note_front": ["便签"],
        "cab_door": ["驾驶室门"],
    }
    for key, names in alias_map.items():
        fields[key]["aliases"] = names
    fields["keys"]["search_aliases"] = alias_map["keys"]
    fields["staff"]["dialogue_topics"] = [
        {
            "id": "key_location",
            "keywords": ["钥匙", "黑包", "驾驶室怎么开"],
            "reveal_entity_ids": ["key_hint"],
            "source_block_ids": fields["key_hint"]["source_block_ids"],
        }
    ]
    kinds = {
        "phone_light": ["light"],
        "torch_light": ["light"],
        "light_off": ["light"],
        "turn_over": ["search"],
        "look_with_phone": ["observe"],
        "look_with_torch": ["observe"],
        "unlock_panel": ["open"],
        "accelerate": ["control"],
        "decelerate": ["control"],
        "clear_path": ["clear"],
        "guided_search": ["search"],
        "sneak": ["pass"],
        "lucky_sneak": ["pass"],
        "evade": ["release", "pass"],
        "warn_mouth": ["converse"],
        "settle_bad_end": ["converse"],
        "crazy_end": ["converse"],
    }
    for entity in package["entities"]:
        for rule in entity["fields"].get("interactions", []):
            if rule["id"] in kinds:
                rule["action_kinds"] = kinds[rule["id"]]
            if rule.get("encounter_operation") == "sound_once":
                rule["action_kinds"] = ["throw"]
            if rule["id"] == "distant_sound_lure":
                rule["required_facts"] = ["sound_distance_over_half_car"]
            if rule["id"].startswith("look_with_"):
                rule["observation_entity_id"] = "clicker"
                rule["observation_effect_id"] = "clicker_seen"
                rule["visibility_any_flags"] = ["torch_light", "phone_light"]
                if rule["id"] == "look_with_phone":
                    rule["required_facts"] = ["visual_target_in_phone_light"]
    effect = fields["clicker"]["sanity_effects"][0]
    effect["perception"] = "visual"
    effect["visibility_any_flags"] = ["torch_light", "phone_light"]
    package["batch18"] = {
        "source_package": str(source.relative_to(ROOT)),
        "source_sha256": digest,
        "runtime_complete": False,
    }
    assert package["numeric_supplement"] == original["numeric_supplement"]
    for key in fields:
        old = next(e["fields"] for e in original["entities"] if e["key"] == key)
        for name in ("combat_template", "check_stats", "source_block_ids"):
            assert fields[key].get(name) == old.get(name)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write(OUTPUT / "package-approved.json", package)
    for name in ("approval.json", "npc-supplement.json"):
        shutil.copyfile(SOURCE / name, OUTPUT / name)
    write(
        OUTPUT / "derivation.json",
        {
            "source_sha256": digest,
            "package_sha256": hashlib.sha256(
                (OUTPUT / "package-approved.json").read_bytes()
            ).hexdigest(),
            "numeric_values_and_approval_unchanged": True,
            "source_accounting_unchanged": package["coverage"] == original["coverage"],
            "runtime_complete": False,
        },
    )
    print(OUTPUT / "package-approved.json")


if __name__ == "__main__":
    build()
