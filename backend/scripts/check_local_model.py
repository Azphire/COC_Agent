"""Run four real, short local-model checks; never contact an external provider."""

import asyncio
import json
import sys
from contextlib import aclosing
from pathlib import Path
from time import perf_counter

import httpx
from pydantic import BaseModel, ConfigDict

# Support the documented `uv run python scripts/check_local_model.py` invocation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.models.base import ModelError, ModelResponse  # noqa: E402
from app.models.factory import create_model, local_ollama_origin  # noqa: E402


class ActionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    target: str | None


ROLL_DICE = {
    "type": "function",
    "function": {
        "name": "roll_dice",
        "description": "请求骰子检定；仅返回参数，不执行骰子或生成结果。",
        "parameters": {
            "type": "object",
            "properties": {
                "sides": {"type": "integer", "description": "骰子面数"},
                "count": {"type": "integer", "description": "骰子数量"},
            },
            "required": ["sides", "count"],
            "additionalProperties": False,
        },
    },
}


def report(check: str, **values: object) -> None:
    print(json.dumps({"check": check, **values}, ensure_ascii=False), flush=True)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ModelError(message)


async def main() -> None:
    settings = Settings()
    require(settings.model_provider == "ollama", "This script only permits the ollama provider")
    origin = local_ollama_origin(settings.model_base_url)
    report("configuration", model=settings.model_name)

    # Empty-prompt loading records load time without generating a fifth answer.
    async with httpx.AsyncClient(
        timeout=settings.model_timeout_seconds, trust_env=False, follow_redirects=False
    ) as http:
        response = await http.get(f"{origin}/api/ps")
        response.raise_for_status()
        was_loaded = any(
            model.get("name") == settings.model_name for model in response.json().get("models", [])
        )
        started = perf_counter()
        response = await http.post(
            f"{origin}/api/generate",
            json={"model": settings.model_name, "stream": False, "keep_alive": "5m"},
        )
        response.raise_for_status()
        loaded = response.json()
        require(loaded.get("done") is True, "Model loading did not finish")
        report(
            "load",
            already_loaded=was_loaded,
            elapsed_seconds=round(perf_counter() - started, 3),
            server_load_seconds=(
                round(loaded["load_duration"] / 1_000_000_000, 3)
                if "load_duration" in loaded
                else None
            ),
        )

    client = create_model(settings)
    try:
        started = perf_counter()
        answer = await client.generate(
            [{"role": "user", "content": "请只回复：连接成功"}], max_tokens=32
        )
        require(isinstance(answer, ModelResponse), "Expected a non-streaming response")
        require(answer.text.strip() == "连接成功", "Plain generation returned an unexpected answer")
        report("plain", ok=True, seconds=round(perf_counter() - started, 3), text=answer.text)

        started = perf_counter()
        stream = await client.generate(
            [{"role": "user", "content": "请用一句简短中文描写雨夜的车站，不要解释。"}],
            stream=True,
            max_tokens=80,
        )
        chunks = []
        async with aclosing(stream):
            async for chunk in stream:
                chunks.append(chunk)
        require(bool(chunks) and bool("".join(chunks).strip()), "No streamed text chunks received")
        report(
            "stream", ok=True, chunks=len(chunks), text="".join(chunks),
            seconds=round(perf_counter() - started, 3),
        )

        answer = await client.generate(
            [{"role": "user", "content": '返回JSON：action为"观察"，target为"车站"。'}],
            response_schema=ActionDecision,
            max_tokens=80,
        )
        require(isinstance(answer, ModelResponse), "Expected a structured response")
        decision = ActionDecision.model_validate_json(answer.text)
        require(
            isinstance(answer.structured, ActionDecision), "Adapter did not validate the schema"
        )
        report("structured", ok=True, output=decision.model_dump())

        answer = await client.generate(
            [
                {"role": "system", "content": "请通过工具发起检定，不要自行掷骰或输出解释。"},
                {"role": "user", "content": "请调用 roll_dice 工具进行一次1D100检定。"},
            ],
            tools=[ROLL_DICE],
            temperature=0.0,
            max_tokens=128,
        )
        require(isinstance(answer, ModelResponse), "Expected a tool-call response")
        require(len(answer.tool_calls) == 1, "Expected exactly one tool call")
        call = answer.tool_calls[0]
        require(call.name == "roll_dice", "Unexpected tool name")
        require(call.arguments == {"sides": 100, "count": 1}, "Incorrect dice arguments")
        require(
            all(type(value) is int for value in call.arguments.values()),
            "Dice arguments must be integers",
        )
        report("tools", ok=True, tool=call.name, arguments=call.arguments)
    finally:
        await client.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        asyncio.run(main())
    except (ModelError, httpx.HTTPError, ValueError) as error:
        report("failed", ok=False, error=str(error))
        raise SystemExit(1) from None
