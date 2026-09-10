from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, SecretStr, StrictBool, StrictInt, StringConstraints

from app.domain.character import DomainModel
from app.rooms.sanity_schemas import SanityState

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
ShortText = Annotated[str, StringConstraints(max_length=2000)]
Resource = Annotated[StrictInt, Field(ge=0, le=100_000)] | None
Visibility = Literal["public", "actor_and_host", "host_only"]


class CharacterRuntimeV1(DomainModel):
    # None means that the published ruleset does not define this resource.
    hp: Resource = None
    mp: Resource = None
    san: Resource = None
    luck: Resource = None
    san_max: Resource = None
    sanity: SanityState = Field(default_factory=SanityState)
    conditions: list[Annotated[str, StringConstraints(min_length=1, max_length=120)]] = Field(
        default_factory=list, max_length=30
    )


class SessionStateV1(DomainModel):
    game_minute: Annotated[StrictInt, Field(ge=0, le=1_000_000)] = 0
    game_round: Annotated[StrictInt, Field(ge=0, le=1_000_000)] = 0
    sanity_day: Annotated[StrictInt, Field(ge=0, le=1_000_000)] = 0
    version: Annotated[StrictInt, Field(ge=1, le=1)] = 1
    scene_title: str = Field(default="", max_length=200)
    scene_summary: ShortText = ""
    round_number: Annotated[StrictInt, Field(ge=0, le=1_000_000)] | None = None
    active_slot_id: UUID | None = None
    characters: dict[UUID, CharacterRuntimeV1] = Field(default_factory=dict, max_length=100)


class CreateRoom(DomainModel):
    name: Name
    host_name: Name = "主机"


class JoinRoom(DomainModel):
    invite_code: SecretStr = Field(min_length=1, max_length=128)
    display_name: Name


class AddMember(DomainModel):
    display_name: Name
    controller_type: Literal["human", "agent"] = "human"


class PatchMember(DomainModel):
    display_name: Name | None = None
    active: Literal[False] | None = None


class ReadyRequest(DomainModel):
    ready: StrictBool
    member_id: UUID | None = None


class PublishCharacter(DomainModel):
    character_id: UUID


class AssignCharacter(DomainModel):
    slot_id: UUID
    member_id: UUID | None = None


class PatchSession(DomainModel):
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    state: SessionStateV1


class MessageRequest(DomainModel):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]
    visibility: Visibility = "public"
    client_request_id: UUID
    actor_member_id: UUID | None = None


class RollRequest(DomainModel):
    expression: str = Field(min_length=1, max_length=32)
    reason: ShortText = ""
    visibility: Visibility = "public"
    client_request_id: UUID
    actor_member_id: UUID | None = None


class CreateSnapshot(DomainModel):
    name: Name


class SnapshotDataV1(DomainModel):
    format_version: Annotated[StrictInt, Field(ge=1, le=1)] = 1
    state: SessionStateV1
    assignments: dict[UUID, UUID | None] = Field(max_length=100)


class SocketAuth(DomainModel):
    type: Literal["auth"]
    credential_type: Literal["host", "member"]
    token: SecretStr = Field(min_length=1, max_length=512)
    after_seq: Annotated[StrictInt, Field(ge=0)] = 0
