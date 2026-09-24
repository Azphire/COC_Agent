from typing import Literal
from uuid import UUID

from pydantic import Field, StrictBool

from app.domain.character import DomainModel
from app.knowledge.schemas import SourceRef
from app.rooms.schemas import Name


class LaunchInput(DomainModel):
    client_request_id: UUID
    preparation_id: UUID
    name: Name = "新的调查"
    character_id: UUID | None = None
    party_batch_id: UUID | None = None
    own_batch_id: UUID | None = None
    ai_count: int | None = Field(default=None, ge=0, le=6)
    reserved_humans: int = Field(default=0, ge=0, le=11)
    era: Literal["1920s", "modern"] = "1920s"
    own_handout: str | None = Field(default=None, max_length=80)
    rules: list[SourceRef] | None = Field(default=None, max_length=8)
    handout_acknowledged: StrictBool = False
    acknowledge_unspent: StrictBool = False


class LaunchPatch(DomainModel):
    version: int = Field(ge=1)
    name: Name | None = None
    character_id: UUID | None = None
    party_batch_id: UUID | None = None
    ai_count: int | None = Field(default=None, ge=0, le=6)
    reserved_humans: int | None = Field(default=None, ge=0, le=11)
    era: Literal["1920s", "modern"] | None = None
    own_handout: str | None = Field(default=None, max_length=80)
    rules: list[SourceRef] | None = Field(default=None, max_length=8)
    handout_acknowledged: StrictBool | None = None
    acknowledge_unspent: StrictBool | None = None


class LaunchStart(DomainModel):
    expected_version: int | None = Field(default=None, ge=1)
    expected_room_revision: int | None = Field(default=None, ge=0)


class PlaySessionInput(DomainModel):
    member_id: UUID | None = None


class PlayCommand(PlaySessionInput):
    command: Literal["pause", "resume", "end", "save", "load", "retry", "cancel"]
    name: Name = "游戏存档"
    snapshot_id: UUID | None = None


class PreparationRequirements(DomainModel):
    minimum_players: int | None = Field(default=None, ge=1, le=12)
    maximum_players: int | None = Field(default=None, ge=1, le=12)
    era: Literal["1920s", "modern"] | None = None
    source: str = Field(default="", max_length=1000)
