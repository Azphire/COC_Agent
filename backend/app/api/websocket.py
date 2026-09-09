import asyncio
from json import JSONDecodeError

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from app.auth import host_matches
from app.rooms.schemas import SocketAuth

router = APIRouter()


@router.websocket("/ws")
async def websocket_echo(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        auth = SocketAuth.model_validate_json(await asyncio.wait_for(websocket.receive_text(), 5))
        if auth.credential_type != "host" or not host_matches(
            websocket.app.state.settings, auth.token.get_secret_value()
        ):
            await websocket.close(code=4401)
            return
        await websocket.send_json({"type": "connected", "data": {"message": "WebSocket connected"}})
        while True:
            try:
                message = await websocket.receive_json()
            except JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "data": {"message": "Send a valid JSON message"}}
                )
                continue
            if isinstance(message, dict) and message.get("type") == "auth":
                await websocket.close(code=4400)
                return
            await websocket.send_json({"type": "echo", "data": message})
    except WebSocketDisconnect:
        pass
    except (ValidationError, TimeoutError, ValueError):
        await websocket.close(code=4401)
