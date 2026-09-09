from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.character.repository import CharacterRepository
from app.character.schemas import (
    CreateCharacterRequest,
    FinalizeRequest,
    PatchCharacterRequest,
    PointBuyRequest,
)
from app.character.service import CharacterService
from app.domain.character import Character, CharacterDraft, CharacterExport, CharacterSheet
from app.rules.schemas import RuleSet

router = APIRouter(prefix="/api", tags=["characters"])


def character_service(request: Request) -> CharacterService:
    return CharacterService(
        CharacterRepository(request.app.state.database),
        request.app.state.character_rulesets,
    )


Service = Annotated[CharacterService, Depends(character_service)]


@router.get("/character-rulesets", response_model=list[RuleSet])
async def list_rulesets(service: Service):
    return list(service.rulesets.values())


@router.get("/character-rulesets/{ruleset_id}", response_model=RuleSet)
async def get_ruleset(ruleset_id: str, service: Service):
    return service.ruleset(ruleset_id, require_enabled=False)


@router.get("/characters", response_model=list[Character])
async def list_characters(service: Service):
    return await service.repository.list_all()


@router.post("/characters/random", response_model=CharacterDraft, status_code=201)
async def create_random(body: CreateCharacterRequest, service: Service):
    return await service.create_random(body)


@router.post("/characters/point-buy", response_model=CharacterDraft, status_code=201)
async def create_point_buy(body: PointBuyRequest, service: Service):
    return await service.create_point_buy(body)


@router.post("/characters/import", response_model=CharacterDraft, status_code=201)
async def import_character(body: CharacterExport, service: Service):
    return await service.import_character(body)


@router.get("/characters/{character_id}", response_model=Character)
async def get_character(character_id: UUID, service: Service):
    return await service.get(character_id)


@router.patch("/characters/{character_id}", response_model=Character)
async def update_character(character_id: UUID, body: PatchCharacterRequest, service: Service):
    return await service.patch(character_id, body)


@router.post("/characters/{character_id}/finalize", response_model=CharacterSheet)
async def finalize_character(character_id: UUID, body: FinalizeRequest, service: Service):
    return await service.finalize(character_id, body.version)


@router.get("/characters/{character_id}/export", response_model=CharacterExport)
async def export_character(character_id: UUID, service: Service):
    return await service.export(character_id)
