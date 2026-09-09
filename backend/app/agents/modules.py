"""Small original modules, validated and frozen on room binding."""

import hashlib
import json
from pathlib import Path

import yaml
from pydantic import Field, model_validator

from app.agents.schemas import Difficulty, Text
from app.domain.character import DomainModel


class Scene(DomainModel):
    id: str
    title: str
    public_description: Text
    keeper_notes: str = ""


class NPC(DomainModel):
    id: str
    name: str
    public_description: Text
    keeper_notes: str = ""


class SuggestedCheck(DomainModel):
    kind: str = "skill"
    name: str
    difficulty: Difficulty = "regular"


class Prerequisites(DomainModel):
    scene_id: str | None = None
    clue_ids: list[str] = Field(default_factory=list)
    successful_check: SuggestedCheck | None = None


class Clue(DomainModel):
    id: str
    title: str
    content: Text
    visibility: str = "hidden"
    prerequisites: Prerequisites = Field(default_factory=Prerequisites)


class Completion(DomainModel):
    clue_ids: list[str] = Field(min_length=1)
    scene_id: str
    public_text: Text


class Module(DomainModel):
    id: str
    title: str
    version: str
    public_introduction: Text
    keeper_brief: Text
    initial_scene: str
    scenes: list[Scene] = Field(min_length=1, max_length=3)
    npcs: list[NPC] = Field(default_factory=list, max_length=2)
    clues: list[Clue] = Field(default_factory=list, max_length=5)
    suggested_checks: list[SuggestedCheck]
    completion_conditions: Completion | None

    @model_validator(mode="after")
    def references(self):
        scenes, clues = {s.id for s in self.scenes}, {c.id for c in self.clues}
        if len(scenes) != len(self.scenes) or len(clues) != len(self.clues):
            raise ValueError("模组 ID 重复")
        if len({n.id for n in self.npcs}) != len(self.npcs):
            raise ValueError("NPC ID 重复")
        if self.initial_scene not in scenes or (
            self.completion_conditions and self.completion_conditions.scene_id not in scenes
        ):
            raise ValueError("场景引用不存在")
        if self.completion_conditions and not set(self.completion_conditions.clue_ids) <= clues:
            raise ValueError("结束条件线索不存在")
        for clue in self.clues:
            pre = clue.prerequisites
            if clue.visibility not in {"public", "hidden", "keeper_only"}:
                raise ValueError("线索可见性无效")
            if pre.scene_id and pre.scene_id not in scenes:
                raise ValueError("线索场景不存在")
            if not set(pre.clue_ids) <= clues or clue.id in pre.clue_ids:
                raise ValueError("线索前置条件无效")
            if clue.visibility == "public" and (pre.clue_ids or pre.successful_check):
                raise ValueError("公开初始线索不能有未满足条件")
        for check in self.suggested_checks + [
            c.prerequisites.successful_check for c in self.clues if c.prerequisites.successful_check
        ]:
            if check.kind not in {"attribute", "skill"}:
                raise ValueError("检定类别无效")
        return self


def content_hash(document: dict) -> str:
    return hashlib.sha256(
        json.dumps(document, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()


def load_modules(directory: Path | None = None) -> dict[str, Module]:
    directory = directory or Path(__file__).with_name("definitions")
    modules = {}
    for path in sorted(directory.glob("*.yaml")):
        module = Module.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
        if module.id in modules:
            raise ValueError("模组 ID 重复")
        modules[module.id] = module
    return modules


def public_module(record) -> dict:
    module = Module.model_validate(record.document)
    scene = next(s for s in module.scenes if s.id == record.state["scene_id"])
    return {
        "id": module.id,
        "title": module.title,
        "version": module.version,
        "content_hash": record.content_hash,
        "public_introduction": module.public_introduction,
        "scene": scene.model_dump(exclude={"keeper_notes"}),
        "npcs": [n.model_dump(exclude={"keeper_notes"}) for n in module.npcs],
        "clues": [
            {"id": c.id, "title": c.title, "content": c.content}
            for c in module.clues
            if c.id in record.state["revealed_clues"] and c.visibility != "keeper_only"
        ],
        "completed": record.state.get("completed", False),
    }
