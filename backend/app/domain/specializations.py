"""Character-owned skill identities; values always come from the ruleset."""

import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def normalized_name(name):
    return "".join(unicodedata.normalize("NFKC", name).casefold().split())


class CustomSpecialization(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(
        pattern=r"^custom_(language|art_craft|science|pilot|survival|lore)_[a-f0-9]{32}$"
    )
    group: Literal["language", "art_craft", "science", "pilot", "survival", "lore"]
    name: str = Field(min_length=1, max_length=40)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value):
        if any(unicodedata.category(c).startswith("C") for c in value):
            raise ValueError("专业名称不能包含控制字符")
        value = unicodedata.normalize("NFKC", value).strip()
        if not value or len(value) > 40:
            raise ValueError("规范化后的专业名称须为 1–40 字")
        if any(c in value for c in "()（）<>[]{}"):
            raise ValueError("只填写专业名称，不含类别前缀或括号")
        return value

    @model_validator(mode="after")
    def matching_id(self):
        if not self.id.startswith(f"custom_{self.group}_"):
            raise ValueError("专业 ID 与类别不一致")
        return self
