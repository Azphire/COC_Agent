"""Closed tool registry. Only this layer translates model plans into domain commands."""

import re
from dataclasses import dataclass
from uuid import uuid4

from pydantic import ValidationError
from sqlalchemy import select

from app.agents import schemas as s
from app.agents.modules import Module, content_hash, public_module
from app.knowledge.schemas import ExcerptArgs, SearchArgs
from app.memory.service import write_memory
from app.module_ir.schemas import (
    LookupArgs,
    ModuleSearchArgs,
    NodeArgs,
    SceneToolArgs,
    TransitionRequest,
)
from app.persistence.agent_models import (
    AgentCycle,
    AgentMemory,
    AgentRun,
    CheckRecord,
    ProfileRecord,
    ToolReceipt,
)
from app.preparation.schemas import EntityArgs, ProposalArgs
from app.rooms.sanity_schemas import SanityRequest
from app.rooms.service import RoomError, require


@dataclass(frozen=True)
class ToolDefinition:
    arguments: type
    roles: frozenset[str]
    description: str
    read_only: bool = False


KEEPER, INVESTIGATOR, BOTH = (
    frozenset({"keeper"}),
    frozenset({"investigator"}),
    frozenset({"keeper", "investigator"}),
)
TOOLS = {
    "request_sanity_check": ToolDefinition(
        SanityRequest,
        KEEPER,
        "请求批准实体的 SAN 效果；复制 effect_id 和具体遭遇事件 seq，不接受损失点数",
    ),
    "get_current_scene": ToolDefinition(s.Empty, KEEPER, "按权威导航读取当前场景", read_only=True),
    "list_scene_contents": ToolDefinition(
        s.Empty, KEEPER, "当前场景内的节点与区块索引", read_only=True
    ),
    "open_module_node": ToolDefinition(NodeArgs, KEEPER, "读取当前、祖先或显式关联节点的受限内容"),
    "lookup_module_entity": ToolDefinition(
        LookupArgs, KEEPER, "按实体 ID 或准确标题查找，返回同名候选"
    ),
    "list_scene_transitions": ToolDefinition(
        s.Empty, KEEPER, "列出当前场景的批准转换及条件", read_only=True
    ),
    "get_public_scene": ToolDefinition(s.Empty, BOTH, "读取当前公开场景", read_only=True),
    "list_public_entities": ToolDefinition(s.Empty, BOTH, "读取公开调查板", read_only=True),
    "lookup_public_entity": ToolDefinition(LookupArgs, BOTH, "仅查询已公开实体"),
    "inspect_approved_entities": ToolDefinition(
        s.Empty, KEEPER, "查看当前房间批准实体与公开条件", read_only=True
    ),
    "inspect_public_entities": ToolDefinition(
        s.Empty, BOTH, "读取与真人调查板相同的公开实体", read_only=True
    ),
    "reveal_entity": ToolDefinition(
        EntityArgs, KEEPER, "按批准条件揭示当前房间实体，不接受自行编写的文本"
    ),
    "transition_scene": ToolDefinition(
        SceneToolArgs, KEEPER, "按导航 revision 转场；缺少批准转换或条件时等待主机"
    ),
    "propose_module_fact": ToolDefinition(
        ProposalArgs, KEEPER, "用当前 run 模组证据提出未批准事实，暂停等待主机"
    ),
    "request_host_review": ToolDefinition(
        ProposalArgs, KEEPER, "提交实体公开或场景转换的主机审阅请求"
    ),
    "search_rules": ToolDefinition(SearchArgs, BOTH, "检索房间绑定版本的公开规则；返回可引用证据"),
    "search_module": ToolDefinition(
        ModuleSearchArgs, KEEPER, "默认仅搜索当前场景；linked_nodes 必须显式关联；global 需主机授权"
    ),
    "get_evidence_excerpt": ToolDefinition(
        ExcerptArgs, BOTH, "读取当前 run 已授权获得的证据短摘录"
    ),
    "inspect_public_state": ToolDefinition(
        s.Empty, BOTH, "读取当前公开场景、NPC 和已公开线索", read_only=True
    ),
    "inspect_character": ToolDefinition(s.CharacterArgs, KEEPER, "读取房间成员的真实角色快照"),
    "request_skill_check": ToolDefinition(
        s.CheckRequest, KEEPER, "为角色请求一次服务端检定；不能指定技能值或骰点"
    ),
    "reveal_clue": ToolDefinition(s.ClueArgs, KEEPER, "揭示实际存在且满足前置条件的线索"),
    "update_scene": ToolDefinition(s.SceneArgs, KEEPER, "切换到模组内场景"),
    "send_narration": ToolDefinition(
        s.SpeechArgs, KEEPER, "向玩家发送简短叙事；不得泄漏尚未揭示的线索或 KP 秘密"
    ),
    "write_memory": ToolDefinition(
        s.MemoryArgs, KEEPER, "以来源事件记忆事实；无来源推测只能记为 belief"
    ),
    "inspect_own_character": ToolDefinition(
        s.Empty, INVESTIGATOR, "读取自己席位的角色卡", read_only=True
    ),
    "speak": ToolDefinition(
        s.SpeechArgs, INVESTIGATOR, "本轮发言一次，与 propose_action 合计最多一次"
    ),
    "propose_action": ToolDefinition(s.SpeechArgs, INVESTIGATOR, "本轮提出行动一次，不触发新回合"),
    "write_private_memory": ToolDefinition(
        s.PrivateMemoryArgs, INVESTIGATOR, "将自己的推断记为私有 belief"
    ),
}


def definitions(role, *, structure_navigation=False):
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": spec.description,
                "parameters": (
                    TransitionRequest
                    if structure_navigation and name == "transition_scene"
                    else spec.arguments
                ).model_json_schema(),
            },
        }
        for name, spec in TOOLS.items()
        if role in spec.roles
    ]


def normalize_arguments(name, arguments):
    spec = TOOLS.get(name)
    if spec and spec.read_only and not spec.arguments.model_fields:
        return {}
    return arguments


def validate(name, arguments, role):
    require(name in TOOLS, "工具未注册", 422)
    spec = TOOLS[name]
    require(role in spec.roles, "此角色无权使用该工具", 403)
    return spec.arguments.model_validate(normalize_arguments(name, arguments))


def ensure_public_text(module_record, text):
    """Reject literal private material before it can enter public events.

    This complements prompt separation; it is not a semantic classifier for paraphrases.
    """
    module = Module.model_validate(module_record.document)
    hidden = [module.keeper_brief, *[n.keeper_notes for n in module.npcs]]
    hidden += [
        c.content
        for c in module.clues
        if c.id not in module_record.state["revealed_clues"] or c.visibility == "keeper_only"
    ]
    hidden += [sc.keeper_notes for sc in module.scenes]
    hidden += [
        sc.public_description for sc in module.scenes if sc.id != module_record.state["scene_id"]
    ]
    for secret in hidden:
        for phrase in re.split(r"[，。；：、\n]", secret):
            if len(phrase.strip()) >= 6 and phrase.strip() in text:
                require(False, "输出包含尚未公开的模组信息", 403)


class AgentTools:
    def __init__(self, service):
        self.service = service

    async def execute(self, room_id, run_id, index, name, arguments, *, recovery=False):
        async def operation(session, room):
            run = await session.get(AgentRun, run_id)
            require(run and run.room_id == room.id, "运行记录不属于此房间", 403)
            profile = await session.get(ProfileRecord, run.profile_id)
            bindings = await self.service.bindings(session, room.id)
            binding = next(
                (
                    b
                    for b in bindings
                    if b.member_id == run.actor_member_id and b.profile_id == run.profile_id
                ),
                None,
            )
            require(binding, "Agent 绑定不存在或已停用", 403)
            member = next(
                (
                    m
                    for m in await self.service.rooms.members(session, room)
                    if m.id == binding.member_id
                ),
                None,
            )
            require(member and member.active, "Agent 成员已失活", 403)
            cycle = await session.get(AgentCycle, run.cycle_id)
            require(cycle.status == "running" and room.status == "running", "回合已停止")
            key = f"{run_id}:{index}"
            fingerprint = content_hash({"name": name, "arguments": arguments})
            previous = await session.get(ToolReceipt, key)
            if recovery and previous and not previous.result.get("ok"):
                require(
                    previous.result.get("code") in {"context_missing", "revision_conflict"},
                    "此工具错误不能自动重试",
                )
                key += ":recovery"
                previous = await session.get(ToolReceipt, key)
            if previous:
                require(
                    previous.run_id == run_id
                    and previous.room_id == room.id
                    and previous.request_hash == fingerprint,
                    "工具幂等键已用于不同参数",
                )
                if previous.result.get("ok"):
                    from app.agents.adjudication_schemas import AdjudicationRecord, RecoveryDecision
                    from app.persistence.adjudication_models import ActionPlanRecord

                    plan_record = await session.get(ActionPlanRecord, run.cycle_id)
                    if plan_record:
                        document = AdjudicationRecord.model_validate(plan_record.document)
                        if not any(
                            r.action == "receipt" and r.tool_index == index
                            for r in document.recoveries
                        ):
                            document.recoveries.append(
                                RecoveryDecision(
                                    error="already_applied",
                                    action="receipt",
                                    tool_index=index,
                                    succeeded=True,
                                )
                            )
                            plan_record.document = document.model_dump(mode="json")
                return previous.result
            require(0 <= index < 4, "每次 Agent run 最多四个工具", 422)
            try:
                args = validate(
                    name, await self.service.sanitize(session, room, arguments), profile.role
                )
                async with session.begin_nested():
                    result = await self.dispatch(session, room, run, binding, profile, name, args)
                result = {"ok": True, "tool": name, "data": result}
            except ValidationError:
                result = {
                    "ok": False,
                    "tool": name,
                    "error": "工具参数不合法",
                    "code": "invalid_arguments",
                }
            except RoomError as error:
                from app.agents.action_policy import error_category

                result = {
                    "ok": False,
                    "tool": name,
                    "error": error.message,
                    "code": error_category(error),
                }
            except Exception:
                result = {
                    "ok": False,
                    "tool": name,
                    "error": "工具暂时无法完成，请主机处理",
                    "code": "internal_error",
                }
            if not result["ok"]:
                # A savepoint rollback expires objects changed during dispatch, including
                # the navigation revision audit. Refresh explicitly before async ORM reads.
                for record in (room, run, cycle, binding, profile):
                    await session.refresh(record)
            result = await self.service.sanitize(session, room, result)
            session.add(
                ToolReceipt(
                    id=key, room_id=room.id, run_id=run_id, request_hash=fingerprint, result=result
                )
            )
            run.tool_results = [*run.tool_results, {"idempotency_key": key, **result}]
            if not result["ok"]:
                self.service.rooms.append(
                    session,
                    room,
                    "agent.tool_rejected",
                    binding.member_id,
                    {
                        "cycle_id": run.cycle_id,
                        "tool": name,
                        "error": result["error"],
                        "code": result["code"],
                    },
                    "host_only",
                )
            cycle.state = {
                **cycle.state,
                "tool_count": cycle.state["tool_count"] + 1,
                "tool_results": [*cycle.state["tool_results"], key],
            }
            return result

        return await self.service.mutate(room_id, operation)

    async def dispatch(self, session, room, run, binding, profile, name, args):
        service, rooms = self.service, self.service.rooms
        from app.agents.action_policy import STATE_TOOLS
        from app.persistence.adjudication_models import ActionPlanRecord

        action_record = await session.get(ActionPlanRecord, run.cycle_id)
        if action_record and name in STATE_TOOLS:
            cycle = await session.get(AgentCycle, run.cycle_id)
            _, doc, _, _ = await service.adjudication.validate(
                session, room, cycle, after_check=True
            )
            require(
                any(
                    a.tool.name == name
                    and a.tool.arguments == args.model_dump(mode="json", exclude_none=True)
                    or a.tool.name == name
                    and a.tool.arguments == args.model_dump(mode="json")
                    for a in doc.validation.approved_actions
                ),
                "precondition_failed：工具未通过行动计划验证",
            )
        navigation = await service.navigation.state(session, room.id)
        if navigation:
            cycle = await session.get(AgentCycle, run.cycle_id)
            await service.navigation.check_cycle(session, room, cycle)
        if name == "get_public_scene":
            return await service.navigation.public_scene(session, room)
        if name in {"list_public_entities", "inspect_public_entities"}:
            return await service.entities.public(session, room.id)
        if name in {"lookup_module_entity", "lookup_public_entity"}:
            return await service.module_context.lookup(
                session, room, args.query, public=name == "lookup_public_entity"
            )
        if name == "open_module_node":
            return await service.module_context.open_node(session, room, run, args)
        if name in {"get_current_scene", "list_scene_contents", "list_scene_transitions"}:
            require(navigation, "module_structure_missing", 422)
            resolved = await service.module_context.resolve(session, room, "keeper", budget=2000)
            if name == "get_current_scene":
                return resolved["module"]["current_scene"]
            if name == "list_scene_transitions":
                return resolved["module"]["outgoing_transitions"]
            _, _, ir, local, _, _ = await service.module_context.allowed(session, room)
            return {
                "nodes": local,
                "blocks": [
                    {"block_id": b.block_id, "node_id": b.node_id, "type": b.block_type}
                    for b in ir.blocks
                    if b.node_id in local
                ],
            }
        if name == "search_module" and navigation:
            return await service.module_context.search(session, room, run, args)
        if name == "transition_scene" and navigation:
            if args.scene_id:
                return await service.entities.transition(session, room, run, args.scene_id)
            return await service.navigation.transition(
                session, room, args.model_dump(exclude={"scene_id"}), run=run
            )
        if name in {"search_rules", "search_module"}:
            evidence, _ = await service.knowledge.search(
                session,
                room,
                run_id=run.id,
                profile=profile,
                actor_id=binding.member_id,
                query=args.query,
                kind="rules" if name == "search_rules" else "module",
                top_k=args.top_k,
                scene_id=getattr(args, "scene_id", None),
                entity_id=getattr(args, "entity_id", None),
            )
            return {"evidence": evidence}
        if name == "get_evidence_excerpt":
            return await service.knowledge.get_excerpt(
                session, room, run, args.evidence_id, profile
            )
        module = await service.module(session, room.id)
        require(module and module.enabled, "模组没有启用")
        prepared = await service.entities.binding(session, room.id)
        if name == "inspect_public_entities":
            return await service.entities.public(session, room.id)
        if name == "inspect_approved_entities":
            if navigation:
                return (await service.module_context.resolve(session, room, "keeper", budget=3000))[
                    "module"
                ]["approved_entities"]
            return await service.entities.host(session, room.id)
        if name in {"propose_module_fact", "request_host_review"}:
            return await service.entities.propose(session, room, run, args)
        if name == "reveal_entity" or prepared and name == "reveal_clue":
            entity_id = args.entity_id if name == "reveal_entity" else args.clue_id
            return await service.entities.reveal(
                session, room, entity_id, binding.member_id, run.cycle_id
            )
        if name == "transition_scene" or prepared and name == "update_scene":
            return await service.entities.transition(session, room, run, args.scene_id)
        if name == "inspect_public_state":
            return {
                **public_module(module),
                "public_entities": await service.entities.public(session, room.id),
            }
        if name in {"inspect_character", "inspect_own_character"}:
            member_id = str(args.member_id) if name == "inspect_character" else binding.member_id
            slot = next(
                (slot for slot in await rooms.slots(session, room) if slot.member_id == member_id),
                None,
            )
            require(slot is not None, "角色不在本房间", 404)
            return {
                **slot.character_snapshot,
                "runtime": room.session_state.get("characters", {}).get(slot.id, {}),
            }
        if name == "request_sanity_check":
            return await service.sanity.encounters.propose(session, room, args, run)
        if name == "request_skill_check":
            if args.visibility != "host_only":
                ensure_public_text(module, args.reason)
            record = await service.request_check(session, room, run, args)
            return {"check_id": record.id, "status": record.status}
        if name == "reveal_clue":
            definition = Module.model_validate(module.document)
            clue = next((c for c in definition.clues if c.id == args.clue_id), None)
            require(clue and clue.visibility != "keeper_only", "线索不存在或仅供 KP 阅读", 422)
            revealed = module.state["revealed_clues"]
            if clue.id in revealed:
                return {"clue_id": clue.id, "already_revealed": True}
            pre = clue.prerequisites
            require(
                not pre.scene_id or pre.scene_id == module.state["scene_id"], "线索不在当前场景"
            )
            require(set(pre.clue_ids) <= set(revealed), "线索前置条件未满足")
            if pre.successful_check:
                records = list(
                    await session.scalars(
                        select(CheckRecord).where(
                            CheckRecord.room_id == room.id,
                            CheckRecord.status == "resolved",
                            CheckRecord.cycle_id == run.cycle_id,
                        )
                    )
                )
                require(
                    any(
                        c.document.get("clue_id") == clue.id and c.document["result"]["passed"]
                        for c in records
                    ),
                    "必须通过关联的真实检定才可揭示此线索",
                )
            module.state = {**module.state, "revealed_clues": [*revealed, clue.id]}
            event = rooms.append(
                session,
                room,
                "clue.revealed",
                binding.member_id,
                {
                    "clue_id": clue.id,
                    "title": clue.title,
                    "content": clue.content,
                    "cycle_id": run.cycle_id,
                },
            )
            session.add(
                AgentMemory(
                    id=str(uuid4()),
                    room_id=room.id,
                    profile_id=None,
                    kind="observation",
                    scope="public",
                    content=clue.content,
                    source_event_ids=[event.seq],
                    salience=10,
                    active=True,
                )
            )
            await self.complete(session, room, module)
            return {"clue_id": clue.id, "event_seq": event.seq}
        if name == "update_scene":
            await service.adjudication.guard_transition(session, room, run, args.scene_id)
            definition = Module.model_validate(module.document)
            scene = next((sc for sc in definition.scenes if sc.id == args.scene_id), None)
            require(scene is not None, "模组中没有此场景", 422)
            if module.state["scene_id"] == scene.id:
                return {"scene_id": scene.id, "unchanged": True}
            module.state = {**module.state, "scene_id": scene.id}
            room.session_state = {
                **room.session_state,
                "scene_title": scene.title,
                "scene_summary": scene.public_description,
            }
            rooms.append(
                session,
                room,
                "scene.updated",
                binding.member_id,
                {
                    "scene_id": scene.id,
                    "scene_title": scene.title,
                    "scene_summary": scene.public_description,
                    "cycle_id": run.cycle_id,
                },
            )
            await self.complete(session, room, module)
            return {"scene_id": scene.id}
        if name in {"send_narration", "speak", "propose_action"}:
            if name == "send_narration":
                # Do not publish or forward text written with access to KP secrets.
                # A separate public-context run writes the actual narration after tools/checks.
                return {"narration_requested": True}
            ensure_public_text(module, args.text)
            if name in {"speak", "propose_action"}:
                prior = [
                    r
                    for r in run.tool_results
                    if r.get("ok") and r.get("tool") in {"speak", "propose_action"}
                ]
                require(not prior, "每个队友每轮只能发言或行动一次")
            event = rooms.append(
                session,
                room,
                {
                    "send_narration": "keeper.narration",
                    "speak": "agent.spoke",
                    "propose_action": "agent.action_proposed",
                }[name],
                binding.member_id,
                {
                    "text": args.text,
                    "cycle_id": run.cycle_id,
                    "actor_name": profile.document["name"],
                    "controller_type": "agent",
                },
            )
            return {"event_seq": event.seq}
        if name in {"write_memory", "write_private_memory"}:
            if name == "write_private_memory":
                args = s.MemoryArgs(**args.model_dump(), kind="belief", scope="agent_private")
            if args.scope == "public":
                ensure_public_text(module, args.content)
            memory = await write_memory(session, rooms, room, binding, profile, args)
            rooms.append(
                session,
                room,
                "agent.memory_written",
                binding.member_id,
                {"memory_id": memory.id, "kind": memory.kind},
                "host_only",
            )
            return {"memory_id": memory.id}
        require(False, "未实现工具", 422)

    async def complete(self, session, room, module):
        condition = Module.model_validate(module.document).completion_conditions
        if condition is None:
            return
        if (
            not module.state.get("completed")
            and module.state["scene_id"] == condition.scene_id
            and set(condition.clue_ids) <= set(module.state["revealed_clues"])
        ):
            module.state = {**module.state, "completed": True}
            self.service.rooms.append(
                session,
                room,
                "module.completed",
                room.host_member_id,
                {"text": condition.public_text},
            )
