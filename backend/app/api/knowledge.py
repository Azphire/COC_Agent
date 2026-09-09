import asyncio
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter
from pydantic import Field
from sqlalchemy import select

from app.api.agents import Service, Token, host
from app.domain.character import DomainModel
from app.knowledge.repository import source_view
from app.knowledge.schemas import KnowledgeBinding, SearchArgs, SourceRef
from app.persistence.agent_models import AgentRun
from app.persistence.knowledge_models import ClaimRecord, RetrievalRecord
from app.rooms.service import require

router = APIRouter(prefix="/api")


class IndexInput(DomainModel):
    kind: Literal["rules", "modules", "all"] = "all"


class QueryInput(SearchArgs):
    kind: Literal["rules", "module"] = "rules"
    sources: list[SourceRef] = Field(default_factory=list, max_length=8)


@router.get("/knowledge/sources", dependencies=host)
async def sources(svc: Service):
    return [source_view(s) for s in svc.knowledge.repository.sources()]


@router.get("/knowledge/sources/{source_id}", dependencies=host)
async def source(source_id: str, svc: Service):
    item = svc.knowledge.repository.source(source_id)
    require(item, "来源不存在", 404)
    return source_view(item)


@router.get("/knowledge/index/status", dependencies=host)
async def status(svc: Service):
    return {"indexing": svc.knowledge.index_lock.locked(), **svc.knowledge.repository.verify()}


@router.post("/knowledge/index", dependencies=host)
async def index(body: IndexInput, svc: Service):
    require(not svc.knowledge.index_lock.locked(), "索引正在进行", 409)
    async with svc.knowledge.index_lock:
        result = await asyncio.to_thread(svc.knowledge.indexer.index, body.kind)
    return [{**r, "source": source_view(r["source"])} if "source" in r else r for r in result]


@router.post("/knowledge/query", dependencies=host)
async def query(body: QueryInput, svc: Service):
    refs = [r.model_dump() for r in body.sources]
    if body.kind == "module":
        require(len(refs) == 1, "模组查询必须指定唯一来源及 hash", 422)
    if not refs:
        refs = [
            {"source_id": s.source_id, "source_hash": s.source_hash}
            for s in svc.knowledge.repository.sources()
            if s.kind != "module"
        ]
    return svc.knowledge.retriever.search(
        body.query,
        refs=refs,
        run_id=str(uuid4()),
        kind=body.kind,
        keeper=True,
        top_k=body.top_k,
        scene_id=body.scene_id,
        entity_id=body.entity_id,
    )


@router.get("/rooms/{room_id}/knowledge")
async def binding(room_id: UUID, svc: Service, token: Token):
    async with svc.rooms.database.sessions() as session:
        room = await svc.rooms.room(session, room_id)
        await svc.rooms.identity(session, room, token)
        return await svc.knowledge.view(session, room.id)


@router.patch("/rooms/{room_id}/knowledge")
async def bind(room_id: UUID, body: KnowledgeBinding, svc: Service, token: Token):
    return await svc.rooms.command(room_id, token, "agent.knowledge", body)


@router.post("/rooms/{room_id}/knowledge/query")
async def room_query(room_id: UUID, body: SearchArgs, svc: Service, token: Token):
    async with svc.rooms.database.sessions() as session:
        room = await svc.rooms.room(session, room_id)
        await svc.rooms.identity(session, room, token)
        bound = await svc.knowledge.binding(session, room.id)
        require(bound and bound["enabled"], "房间未启用知识来源", 422)
        return svc.knowledge.retriever.search(
            body.query,
            refs=bound["rules"],
            run_id=str(uuid4()),
            kind="rules",
            keeper=False,
            top_k=body.top_k,
        )


@router.get("/rooms/{room_id}/agent-runs/{run_id}/retrievals")
async def retrievals(room_id: UUID, run_id: UUID, svc: Service, token: Token):
    async with svc.rooms.database.sessions() as session:
        room = await svc.rooms.room(session, room_id)
        identity = await svc.rooms.identity(session, room, token)
        require(identity.is_host, "仅主机可查看检索审计", 403)
        run = await session.get(AgentRun, str(run_id))
        require(run and run.room_id == room.id, "运行不存在", 404)
        rows = await session.scalars(
            select(RetrievalRecord)
            .where(RetrievalRecord.room_id == room.id, RetrievalRecord.run_id == str(run_id))
            .order_by(RetrievalRecord.created_at)
        )
        return [
            {
                "id": r.id,
                "query": r.query,
                "source_filters": r.source_filters,
                "evidence": r.evidence,
                "injected_ids": r.injected_ids,
                "latency_ms": r.latency_ms,
            }
            for r in rows
        ]


@router.get("/rooms/{room_id}/evidence/{evidence_id}")
async def evidence(room_id: UUID, evidence_id: str, svc: Service, token: Token):
    async with svc.rooms.database.sessions() as session:
        room = await svc.rooms.room(session, room_id)
        identity = await svc.rooms.identity(session, room, token)
        if identity.is_host:
            rows = await session.scalars(
                select(RetrievalRecord).where(RetrievalRecord.room_id == room.id)
            )
            for row in rows:
                for item in row.evidence:
                    if item["evidence_id"] == evidence_id:
                        return item
        else:
            rows = await session.scalars(
                select(ClaimRecord).where(
                    ClaimRecord.room_id == room.id, ClaimRecord.event_seq.is_not(None)
                )
            )
            for row in rows:
                if row.document["visibility"] == "public" and row.document["category"] == "rule":
                    for item in row.document["sources"]:
                        if item["evidence_id"] == evidence_id:
                            return item
        require(False, "证据不存在或不可见", 404)
