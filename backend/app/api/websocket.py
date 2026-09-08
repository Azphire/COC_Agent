from json import JSONDecodeError

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/ws")
async def websocket_echo(websocket: WebSocket) -> None:
    await websocket.accept()
    await websocket.send_json({"type": "connected", "data": {"message": "WebSocket connected"}})
    try:
        while True:
            try:
                message = await websocket.receive_json()
            except JSONDecodeError:
                await websocket.send_json(
                    {"type": "error", "data": {"message": "Send a valid JSON message"}}
                )
                continue
            await websocket.send_json({"type": "echo", "data": message})
    except WebSocketDisconnect:
        pass
