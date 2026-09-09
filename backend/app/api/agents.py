"""Host-only profiles/debugging; room actions retain multiplayer authentication."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select

from app.agents import schemas as s
from app.agents.security import scrub
from app.auth import require_host
from app.domain.character import utc_now
from app.models.base import ModelError
from app.persistence.agent_models import ProfileRecord, RoomAgentBinding
from app.rooms.service import RoomError, require

router = APIRouter(prefix="/api")


def service(request: Request):
    return request.app.state.agent_service


def credential(request: Request):
    from app.auth import bearer

    return bearer(request)


Service = Annotated[object, Depends(service)]
Token = Annotated[str, Depends(credential)]
host = [Depends(require_host)]


@router.get("/agent-profiles", dependencies=host)
async def profiles(svc: Service):
    async with svc.rooms.database.sessions() as session:
        return [
            p.document
            for p in await session.scalars(select(ProfileRecord).order_by(ProfileRecord.created_at))
        ]


@router.post("/agent-profiles/generate-draft", dependencies=host)
async def draft(body: s.DraftRequest, svc: Service):
    safe = scrub(
        body.model_dump(),
        [
            svc.settings.host_admin_token.get_secret_value(),
            svc.settings.model_api_key.get_secret_value(),
        ],
    )
    try:
        result, latency = await svc.model.generate(
            [
                {
                    "role": "system",
                    "content": "生成简短中文 Agent 档案草稿。role 保持指定类型，"
                    "model_preset 为 default。只返回 schema，不输出推理或认证信息。",
                },
                {"role": "user", "content": str(safe)},
            ],
            response_schema=s.ProfileInput,
        )
        profile = result.structured
        require(profile.role == body.role, "草稿类型与请求不一致", 422)
        return {
            "draft": scrub(
                profile.model_dump(),
                [
                    svc.settings.host_admin_token.get_secret_value(),
                    svc.settings.model_api_key.get_secret_value(),
                ],
            ),
            "latency_ms": latency,
            "requires_confirmation": True,
        }
    except ModelError:
        raise RoomError("档案生成失败，输入已保留；可编辑后手动创建", 422) from None


@router.post("/agent-profiles", dependencies=host, status_code=201)
async def create_profile(body: s.ProfileInput, svc: Service):
    profile = s.AgentProfile(
        **scrub(
            body.model_dump(),
            [
                svc.settings.host_admin_token.get_secret_value(),
                svc.settings.model_api_key.get_secret_value(),
            ],
        )
    )
    async with svc.rooms.transaction() as session:
        session.add(
            ProfileRecord(
                id=str(profile.id), role=profile.role, document=profile.model_dump(mode="json")
            )
        )
    return profile


@router.get("/agent-profiles/{profile_id}", dependencies=host)
async def get_profile(profile_id: UUID, svc: Service):
    async with svc.rooms.database.sessions() as session:
        profile = await session.get(ProfileRecord, str(profile_id))
        require(profile, "档案不存在", 404)
        return profile.document


@router.patch("/agent-profiles/{profile_id}", dependencies=host)
async def patch_profile(profile_id: UUID, body: s.ProfileInput, svc: Service):
    async with svc.rooms.transaction() as session:
        profile = await session.get(ProfileRecord, str(profile_id))
        require(profile, "档案不存在", 404)
        bindings = list(
            await session.scalars(
                select(RoomAgentBinding).where(
                    RoomAgentBinding.profile_id == profile.id, RoomAgentBinding.enabled.is_(True)
                )
            )
        )
        require(not bindings, "请先解除房间绑定，再编辑档案")
        updated = s.AgentProfile(
            **body.model_dump(),
            id=profile.id,
            created_at=profile.document["created_at"],
            updated_at=utc_now(),
        )
        profile.document = scrub(
            updated.model_dump(mode="json"),
            [
                svc.settings.host_admin_token.get_secret_value(),
                svc.settings.model_api_key.get_secret_value(),
            ],
        )
        profile.role, profile.updated_at = updated.role, utc_now()
        return profile.document


@router.get("/modules", dependencies=host)
async def modules(svc: Service):
    return [
        {
            "id": m.id,
            "title": m.title,
            "version": m.version,
            "public_introduction": m.public_introduction,
        }
        for m in svc.modules.values()
    ]


@router.get("/agent-model-presets", dependencies=host)
async def presets(svc: Service):
    return [
        {
            "name": "default",
            "provider": svc.settings.model_provider,
            "model": svc.settings.model_name,
            "temperature": svc.settings.model_temperature,
            "context_limit": svc.settings.model_context_limit,
            "output_limit": svc.settings.model_output_limit,
            "timeout": svc.settings.model_timeout_seconds,
            "keep_alive": svc.settings.model_keep_alive,
        }
    ]


@router.get("/rooms/{room_id}/agent-config")
async def config(room_id: UUID, svc: Service, token: Token):
    return await svc.get(room_id, token, "config")


@router.patch("/rooms/{room_id}/agent-config")
async def configure(room_id: UUID, body: s.AgentConfig, svc: Service, token: Token):
    return await svc.rooms.command(room_id, token, "agent.config", body)


@router.post("/rooms/{room_id}/module")
async def bind_module(room_id: UUID, body: s.ModuleInput, svc: Service, token: Token):
    return await svc.rooms.command(room_id, token, "agent.module", body)


@router.post("/rooms/{room_id}/agent-bindings")
async def bind_agent(room_id: UUID, body: s.BindingInput, svc: Service, token: Token):
    return await svc.rooms.command(room_id, token, "agent.binding", body)


@router.delete("/rooms/{room_id}/agent-bindings/{binding_id}")
async def unbind_agent(room_id: UUID, binding_id: UUID, svc: Service, token: Token):
    return await svc.rooms.command(room_id, token, "agent.unbind", target=binding_id)


@router.post("/rooms/{room_id}/actions")
async def action(room_id: UUID, body: s.ActionInput, svc: Service, token: Token):
    return await svc.rooms.command(room_id, token, "agent.action", body)


@router.post("/rooms/{room_id}/clarifications")
async def clarify(room_id: UUID, body: s.ClarificationInput, svc: Service, token: Token):
    return await svc.rooms.command(room_id, token, "agent.action", body)


@router.get("/rooms/{room_id}/cycles/{cycle_id}/plan")
async def keeper_plan(room_id: UUID, cycle_id: UUID, svc: Service, token: Token):
    return await svc.get(room_id, token, "plan", cycle_id)


@router.get("/rooms/{room_id}/cycles/{cycle_id}/validation")
async def validation(room_id: UUID, cycle_id: UUID, svc: Service, token: Token):
    return await svc.get(room_id, token, "validation", cycle_id)


@router.get("/rooms/{room_id}/teammate-behavior")
async def behavior(room_id: UUID, svc: Service, token: Token):
    return await svc.get(room_id, token, "teammate_behavior")


@router.post("/rooms/{room_id}/teammate-behavior/{member_id}/reset")
async def reset_behavior(room_id: UUID, member_id: UUID, svc: Service, token: Token):
    return await svc.rooms.command(room_id, token, "agent.behavior.reset", target=member_id)


@router.get("/rooms/{room_id}/summary-status")
async def summary_status(room_id: UUID, svc: Service, token: Token):
    return await svc.get(room_id, token, "summary_status")


@router.post("/rooms/{room_id}/summary-rebuild")
async def rebuild_summary(room_id: UUID, svc: Service, token: Token):
    await svc.get(room_id, token, "summary_status")
    async with svc.rooms.database.sessions() as session:
        require(not await svc.cycle(session, str(room_id), active=True), "请等待回合结束再重建摘要")
        cycle = await svc.cycle(session, str(room_id))
        require(cycle, "尚无可摘要的行动")
        cycle_id = cycle.id
    await svc.summary_recovery.update(str(room_id), cycle_id, manual=True)
    return await svc.get(room_id, token, "summary_status")


@router.get("/rooms/{room_id}/agent-cycle")
async def cycle(room_id: UUID, svc: Service, token: Token):
    return await svc.get(room_id, token, "cycle")


@router.post("/rooms/{room_id}/agent-cycle/cancel")
async def cancel(room_id: UUID, svc: Service, token: Token):
    return await svc.rooms.command(room_id, token, "agent.cancel")


@router.post("/rooms/{room_id}/agent-cycle/retry")
async def retry(room_id: UUID, svc: Service, token: Token):
    return await svc.rooms.command(room_id, token, "agent.retry")


@router.get("/rooms/{room_id}/checks")
async def checks(room_id: UUID, svc: Service, token: Token):
    return await svc.get(room_id, token, "checks")


@router.post("/rooms/{room_id}/checks/{check_id}/roll")
async def roll(room_id: UUID, check_id: UUID, body: s.Empty, svc: Service, token: Token):
    return await svc.rooms.command(room_id, token, "agent.check.roll", body, check_id)


@router.post("/rooms/{room_id}/checks/{check_id}/cancel")
async def cancel_check(room_id: UUID, check_id: UUID, svc: Service, token: Token):
    return await svc.rooms.command(room_id, token, "agent.check.cancel", target=check_id)


@router.get("/rooms/{room_id}/agent-runs")
async def runs(room_id: UUID, svc: Service, token: Token):
    return await svc.get(room_id, token, "runs")


@router.get("/rooms/{room_id}/agent-runs/{run_id}")
async def run(room_id: UUID, run_id: UUID, svc: Service, token: Token):
    return await svc.get(room_id, token, "run", run_id)


@router.get("/rooms/{room_id}/memories")
async def memory(room_id: UUID, svc: Service, token: Token):
    return await svc.get(room_id, token, "memories")
