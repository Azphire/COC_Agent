from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import select

from app.api.agents import Service, Token, host
from app.api.preparation import host_command
from app.module_ir import schemas as s
from app.persistence.agent_models import AgentCycle
from app.preparation.runtime_schemas import HostModuleAction
from app.rooms.service import RoomError, require

router = APIRouter(prefix="/api")


@router.post("/module-preparations/{preparation_id}/structure/build", dependencies=host)
async def build(preparation_id: UUID, svc: Service):
    try:
        return await svc.structure.build(str(preparation_id))
    except ValueError as error:
        raise RoomError("结构提取失败，请检查本地转换能力和来源 hash", 422) from error


@router.get("/module-preparations/{preparation_id}/structure", dependencies=host)
async def structure(preparation_id: UUID, svc: Service):
    async with svc.rooms.transaction() as session:
        return await svc.structure.view(session, preparation_id)


@router.get("/module-preparations/{preparation_id}/structure/nodes/{node_id}", dependencies=host)
async def preview(preparation_id: UUID, node_id: str, svc: Service):
    async with svc.rooms.transaction() as session:
        return await svc.structure.preview(session, preparation_id, node_id)


@router.patch("/module-preparations/{preparation_id}/structure/nodes/{node_id}", dependencies=host)
async def patch(preparation_id: UUID, node_id: str, body: s.NodePatch, svc: Service):
    async with svc.rooms.transaction() as session:
        try:
            return await svc.structure.patch_node(session, preparation_id, node_id, body)
        except ValueError as error:
            raise RoomError("目录校正不合法，请核对父子关系、类型和引用", 422) from error


@router.post("/module-preparations/{preparation_id}/structure/approve", dependencies=host)
async def approve(preparation_id: UUID, body: s.ApproveStructure, svc: Service):
    async with svc.rooms.transaction() as session:
        try:
            return await svc.structure.approve(session, preparation_id, body)
        except ValueError as error:
            raise RoomError(
                "结构未通过校验：请核对场景类型、公开标题、摘要与 initial scene", 422
            ) from error


@router.get("/module-preparations/{preparation_id}/structure/entity-bindings", dependencies=host)
async def bindings(preparation_id: UUID, svc: Service):
    async with svc.rooms.transaction() as session:
        return await svc.structure.suggestions(session, preparation_id)


@router.post("/module-preparations/{preparation_id}/structure/entity-bindings", dependencies=host)
async def bind_entity(preparation_id: UUID, body: s.BindingInput, svc: Service):
    async with svc.rooms.transaction() as session:
        return await svc.structure.entity_binding(session, preparation_id, body)


@router.delete(
    "/module-preparations/{preparation_id}/structure/entity-bindings/{binding_id}",
    dependencies=host,
)
async def remove_binding(preparation_id: UUID, binding_id: str, svc: Service):
    async with svc.rooms.transaction() as session:
        return await svc.structure.remove_binding(session, preparation_id, binding_id)


@router.get("/module-preparations/{preparation_id}/structure/transitions", dependencies=host)
async def transitions(preparation_id: UUID, svc: Service):
    async with svc.rooms.transaction() as session:
        _, draft = await svc.structure.draft(session, preparation_id)
        return [t.model_dump() for t in draft.transitions]


@router.post("/module-preparations/{preparation_id}/structure/transitions", dependencies=host)
async def add_transition(preparation_id: UUID, body: s.SceneTransition, svc: Service):
    async with svc.rooms.transaction() as session:
        try:
            return await svc.structure.transition(session, preparation_id, body)
        except ValueError as error:
            raise RoomError("转换两端必须为 included scene", 422) from error


@router.patch(
    "/module-preparations/{preparation_id}/structure/transitions/{transition_id}", dependencies=host
)
async def edit_transition(
    preparation_id: UUID, transition_id: str, body: s.SceneTransition, svc: Service
):
    async with svc.rooms.transaction() as session:
        try:
            return await svc.structure.transition(session, preparation_id, body, transition_id)
        except ValueError as error:
            raise RoomError("转换两端必须为 included scene", 422) from error


@router.delete(
    "/module-preparations/{preparation_id}/structure/transitions/{transition_id}", dependencies=host
)
async def delete_transition(preparation_id: UUID, transition_id: str, svc: Service):
    async with svc.rooms.transaction() as session:
        return await svc.structure.transition(session, preparation_id, None, transition_id, True)


@router.get("/rooms/{room_id}/current-scene")
async def current_scene(room_id: UUID, svc: Service, token: Token):
    async with svc.rooms.database.sessions() as session:
        room = await svc.rooms.room(session, room_id)
        identity = await svc.rooms.identity(session, room, token)
        public = await svc.navigation.public_scene(session, room)
        if identity.is_host:
            state = await svc.navigation.state(session, room.id)
            return {**public, "navigation": state.model_dump(mode="json") if state else None}
        return public


@router.get("/rooms/{room_id}/module-navigation")
@router.get("/rooms/{room_id}/module-context-debug")
async def navigation(room_id: UUID, svc: Service, token: Token):
    async with svc.rooms.transaction() as session:
        room = await svc.rooms.room(session, room_id)
        identity = await svc.rooms.identity(session, room, token)
        require(identity.is_host, "仅主机可查看结构导航调试", 403)
        state = await svc.navigation.state(session, room.id)
        if not state:
            return {"enabled": False}
        try:
            context = await svc.module_context.resolve(
                session, room, "keeper", budget=3000, persist_selection=False
            )
            snapshot, _ = await svc.navigation.snapshot(session, state)
            await svc.navigation.refresh(session, room, state, snapshot)
            cycle = await session.scalar(
                select(AgentCycle)
                .where(AgentCycle.room_id == room.id)
                .order_by(AgentCycle.created_at.desc())
                .limit(1)
            )
            audit_keys = {
                "current_scene_node_id",
                "selected_node_ids",
                "selected_block_ids",
                "module_fallback_mode",
                "structure_snapshot_id",
                "module_source_hash",
            }
            return {
                "enabled": True,
                **state.model_dump(mode="json"),
                "context": context,
                "runtime": room.session_state.get("module_runtime", {}),
                "last_cycle_audit": {k: v for k, v in cycle.state.items() if k in audit_keys}
                if cycle
                else None,
                "scenes": [
                    {"node_id": n.node_id, "title": n.title}
                    for n in snapshot.nodes
                    if n.included and n.approved_type == "scene"
                ],
            }
        except RoomError as error:
            if not error.message.startswith("module_structure_missing"):
                raise
            state.module_structure_missing = True
            await svc.navigation.persist(session, state)
            return {
                "enabled": True,
                **state.model_dump(mode="json"),
                "module_structure_missing": True,
            }


@router.post("/rooms/{room_id}/module-navigation/reload")
async def reload_navigation(room_id: UUID, svc: Service, token: Token):
    async def reload(session, room):
        state = await svc.navigation.require_available(session, room.id)
        require(state, "module_structure_missing", 422)
        await svc.navigation.reconcile(session, room)
        return {"reloaded": True, "structure_version": state.structure_version}

    return await host_command(svc, room_id, token, reload)


@router.post("/rooms/{room_id}/scene-transition")
async def transition_scene(room_id: UUID, body: s.TransitionRequest, svc: Service, token: Token):
    return await host_command(
        svc,
        room_id,
        token,
        lambda session, room: svc.navigation.transition(session, room, body, host=True),
    )


@router.post("/rooms/{room_id}/module-search")
async def host_search(room_id: UUID, body: s.ModuleSearchArgs, svc: Service, token: Token):
    return await host_command(
        svc,
        room_id,
        token,
        lambda session, room: svc.module_context.search(session, room, None, body, host=True),
    )


@router.post("/rooms/{room_id}/module-action")
async def module_action(room_id: UUID, body: HostModuleAction, svc: Service, token: Token):
    from app.preparation.runtime import apply_interaction

    return await host_command(
        svc,
        room_id,
        token,
        lambda session, room: apply_interaction(svc, session, room, body, host=True),
    )
