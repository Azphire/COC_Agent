import httpx
from fastapi import APIRouter, Request

from app.models.base import ModelError
from app.models.settings import ModelConfiguration

router = APIRouter(prefix="/api/model")


async def catalogue():
    try:
        async with httpx.AsyncClient(
            timeout=2.0, trust_env=False, follow_redirects=False
        ) as client:
            response = await client.get("http://127.0.0.1:11434/api/tags")
            response.raise_for_status()
            data = response.json()
        entries = data.get("models", []) if isinstance(data, dict) else []
        return {
            "models": sorted(
                {
                    e["name"]
                    for e in entries
                    if isinstance(e, dict) and isinstance(e.get("name"), str)
                }
            ),
            "error": None,
        }
    except (httpx.HTTPError, ValueError, ModelError):
        return {"models": [], "error": "Ollama 未启动或无法读取本机模型列表"}


@router.get("/ollama-models")
async def ollama_models():
    return await catalogue()


@router.get("/config")
async def configuration(request: Request):
    return request.app.state.model_settings.public()


@router.post("/config")
async def save_configuration(body: ModelConfiguration, request: Request):
    return await request.app.state.model_settings.save(body)


@router.get("/status")
async def model_status(request: Request):
    """Refresh never generates or contacts an external provider."""
    manager = request.app.state.model_settings
    config = manager.current
    state = "unconfigured" if not manager.ready() else manager.verification or "unverified"
    error = manager.error
    models = None
    if config.provider == "ollama":
        result = await catalogue()
        models = result["models"]
        expected = config.model if ":" in config.model else config.model + ":latest"
        if manager.ready() and (result["error"] or expected not in models):
            state = "failed"
            error = result["error"] or "所选模型未安装，请选择已有模型"
    return {
        "provider": config.provider,
        "model": config.model,
        "state": state,
        "available": state == "available",
        "error": error,
        "revision": manager.revision,
        "busy": await manager.busy(),
        "models": models,
    }


@router.post("/test")
async def test_model(request: Request):
    """Explicit bounded structured generation. No room, dice or tools are executed."""
    from app.agents.adjudication_schemas import KeeperNarration
    from app.agents.generation_contracts import generation_contract

    svc = request.app.state.agent_service
    manager = request.app.state.model_settings
    context = {"current_scene_reference": "model-test-room"}
    before = len(svc.model.calls)
    try:
        schema = generation_contract(KeeperNarration, context)
        await svc.model.generate(
            [
                {
                    "role": "system",
                    "content": "你是KP。这是隔离结构化试运行。已确认的公开事实："
                    "房间为空，窗户关闭，没有NPC。请将此事实写入public_narration，"
                    "只做一句简短叙述，不添加检定、工具或推测，不输出推理。",
                },
                {"role": "user", "content": "我观察房间。"},
            ],
            response_schema=schema,
        )
    except ModelError:
        pass
    return {
        "provider": manager.current.provider,
        "model": manager.current.model,
        "state": manager.verification or "unconfigured",
        "error": manager.error,
        "calls": [
            {
                k: c[k]
                for k in (
                    "provider",
                    "model",
                    "config_revision",
                    "latency_ms",
                    "attempt",
                    "token_usage",
                    "request_id",
                    "response_model",
                    "error_category",
                    "validation_issues",
                    "schema",
                )
            }
            for c in svc.model.calls[before:]
        ],
    }
