from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select

from app.api.agents import Service, Token, host
from app.persistence.preparation_models import GenerationRun, HostReviewRequest, ModulePreparation
from app.preparation import schemas as s
from app.preparation.service import entity_view
from app.rooms.service import require

router = APIRouter(prefix="/api")


@router.post("/module-preparations", dependencies=host)
async def create(body: s.PreparationInput, svc: Service):
    return await svc.preparation.create(body)


@router.get("/module-preparations", dependencies=host)
async def preparations(svc: Service):
    async with svc.rooms.transaction() as session:
        rows = list(
            await session.scalars(
                select(ModulePreparation).order_by(ModulePreparation.created_at.desc())
            )
        )
        return [
            await svc.preparation.view(session, await svc.preparation.get(session, row.id))
            for row in rows
        ]


@router.get("/module-preparations/{preparation_id}", dependencies=host)
@router.post("/module-preparations/{preparation_id}/refresh-status", dependencies=host)
async def preparation(preparation_id: UUID, svc: Service):
    async with svc.rooms.transaction() as session:
        return await svc.preparation.view(
            session, await svc.preparation.get(session, preparation_id)
        )


@router.patch("/module-preparations/{preparation_id}", dependencies=host)
async def configure(preparation_id: UUID, body: s.PreparationPatch, svc: Service):
    return await svc.preparation.configure(str(preparation_id), body)


@router.post("/module-preparations/{preparation_id}/generate", dependencies=host)
async def generate(preparation_id: UUID, svc: Service):
    return await svc.preparation.start_generation(str(preparation_id))


@router.post("/module-preparations/{preparation_id}/approve", dependencies=host)
async def approve_preparation(preparation_id: UUID, svc: Service):
    return await svc.preparation.approve(str(preparation_id))


@router.get("/module-preparations/{preparation_id}/entities", dependencies=host)
async def entities(preparation_id: UUID, svc: Service):
    async with svc.rooms.database.sessions() as session:
        prep = await svc.preparation.get(session, preparation_id)
        return [entity_view(e) for e in await svc.preparation.entities(session, prep.id)]


@router.get("/module-preparations/{preparation_id}/relations", dependencies=host)
async def relations(preparation_id: UUID, svc: Service):
    async with svc.rooms.database.sessions() as session:
        prep = await svc.preparation.get(session, preparation_id)
        return [entity_view(e) for e in await svc.preparation.relations(session, prep.id)]


@router.get("/module-preparations/{preparation_id}/evidence/{evidence_id}", dependencies=host)
async def evidence(preparation_id: UUID, evidence_id: str, svc: Service):
    async with svc.rooms.database.sessions() as session:
        prep = await svc.preparation.get(session, preparation_id)
        for run in await session.scalars(
            select(GenerationRun).where(GenerationRun.preparation_id == prep.id)
        ):
            for item in run.evidence:
                if item["evidence_id"] == evidence_id:
                    return item
        require(False, "证据不属于此准备任务", 404)


@router.post("/module-entities", dependencies=host)
async def create_entity(body: s.HostEntityInput, svc: Service):
    return await svc.preparation.host_entity(body)


@router.patch("/module-entities/{entity_id}", dependencies=host)
async def edit_entity(entity_id: UUID, body: s.EntityPatch, svc: Service):
    return await svc.preparation.change_entity(str(entity_id), "edit", body)


@router.post("/module-entities/{entity_id}/approve", dependencies=host)
async def approve_entity(entity_id: UUID, svc: Service):
    return await svc.preparation.change_entity(str(entity_id), "approved")


@router.post("/module-entities/{entity_id}/reject", dependencies=host)
async def reject_entity(entity_id: UUID, svc: Service):
    return await svc.preparation.change_entity(str(entity_id), "rejected")


@router.post("/module-entities/{entity_id}/draft", dependencies=host)
async def draft_entity(entity_id: UUID, svc: Service):
    return await svc.preparation.change_entity(str(entity_id), "draft")


@router.post("/module-relations/{relation_id}/approve", dependencies=host)
async def approve_relation(relation_id: UUID, svc: Service):
    return await svc.preparation.change_relation(str(relation_id), "approved")


@router.post("/module-relations/{relation_id}/reject", dependencies=host)
async def reject_relation(relation_id: UUID, svc: Service):
    return await svc.preparation.change_relation(str(relation_id), "rejected")


async def host_command(svc, room_id, token, operation):
    async def mutate(session, room):
        identity = await svc.rooms.identity(session, room, token)
        require(identity.is_host, "仅主机可以处理模组实体与审阅", 403)
        result = await operation(session, room)
        await session.flush()
        return {"room": await svc.rooms.view(session, room, identity), "result": result}

    return await svc.mutate(str(room_id), mutate)


@router.patch("/rooms/{room_id}/module-preparation")
async def bind(room_id: UUID, body: s.BindPreparation, svc: Service, token: Token):
    return await host_command(
        svc,
        room_id,
        token,
        lambda session, room: svc.entities.bind(session, room, body.preparation_id),
    )


@router.get("/rooms/{room_id}/public-entities")
async def public_entities(room_id: UUID, svc: Service, token: Token):
    async with svc.rooms.database.sessions() as session:
        room = await svc.rooms.room(session, room_id)
        await svc.rooms.identity(session, room, token)
        return await svc.entities.public(session, room.id)


@router.get("/rooms/{room_id}/host-entities")
async def host_entities(room_id: UUID, svc: Service, token: Token):
    async with svc.rooms.database.sessions() as session:
        room = await svc.rooms.room(session, room_id)
        identity = await svc.rooms.identity(session, room, token)
        require(identity.is_host, "仅主机可读取批准实体与隐藏状态", 403)
        return await svc.entities.host(session, room.id)


@router.post("/rooms/{room_id}/entities/{entity_id}/reveal")
async def reveal(room_id: UUID, entity_id: UUID, svc: Service, token: Token):
    return await host_command(
        svc,
        room_id,
        token,
        lambda session, room: svc.entities.reveal(
            session, room, str(entity_id), room.host_member_id, host_override=True
        ),
    )


@router.post("/rooms/{room_id}/entities/{entity_id}/correct")
async def correct(room_id: UUID, entity_id: UUID, body: s.Correction, svc: Service, token: Token):
    safe = s.Correction.model_validate(svc.preparation.safe(body.model_dump()))
    return await host_command(
        svc,
        room_id,
        token,
        lambda session, room: svc.entities.correct(session, room, str(entity_id), safe),
    )


@router.get("/rooms/{room_id}/review-requests")
async def reviews(room_id: UUID, svc: Service, token: Token):
    async with svc.rooms.database.sessions() as session:
        room = await svc.rooms.room(session, room_id)
        identity = await svc.rooms.identity(session, room, token)
        require(identity.is_host, "仅主机可查看审阅请求", 403)
        return [
            entity_view(r)
            for r in await session.scalars(
                select(HostReviewRequest)
                .where(HostReviewRequest.room_id == room.id)
                .order_by(HostReviewRequest.created_at)
            )
        ]


async def resolve_review(room_id, review_id, body, svc, token, decision):
    safe = s.ReviewResponse.model_validate(svc.preparation.safe(body.model_dump()))
    result = await host_command(
        svc,
        room_id,
        token,
        lambda session, room: svc.entities.resolve(session, room, str(review_id), decision, safe),
    )
    svc.runtime.schedule(str(room_id))
    return result


@router.post("/rooms/{room_id}/review-requests/{review_id}/approve")
async def approve_review(
    room_id: UUID, review_id: UUID, body: s.ReviewResponse, svc: Service, token: Token
):
    return await resolve_review(room_id, review_id, body, svc, token, "approved")


@router.post("/rooms/{room_id}/review-requests/{review_id}/edit-and-approve")
async def edit_review(
    room_id: UUID, review_id: UUID, body: s.ReviewResponse, svc: Service, token: Token
):
    return await resolve_review(room_id, review_id, body, svc, token, "edited")


@router.post("/rooms/{room_id}/review-requests/{review_id}/reject")
async def reject_review(
    room_id: UUID, review_id: UUID, body: s.ReviewResponse, svc: Service, token: Token
):
    return await resolve_review(room_id, review_id, body, svc, token, "rejected")
