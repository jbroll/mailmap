"""WebSocket server for Thunderbird MailExtension communication."""

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Any

import websockets
from websockets.asyncio.server import Server, ServerConnection

from .categories import get_category_descriptions, load_categories
from .config import WebSocketConfig
from .database import Database
from .protocol import Action, Event, Request, Response, ServerEvent, parse_message

logger = logging.getLogger("mailmap.websocket")


class WebSocketServer:
    """WebSocket server that manages MailExtension connections."""

    def __init__(self, config: WebSocketConfig, db: Database, categories_file: str | Path):
        self.config = config
        self.db = db
        self.categories_file = Path(categories_file)
        self._clients: dict[str, ServerConnection] = {}
        self._pending_requests: dict[str, asyncio.Future[Response]] = {}
        self._server: Server | None = None
        self._running = False

    async def start(self) -> None:
        """Start the WebSocket server."""
        self._running = True
        logger.info(f"Starting WebSocket server on {self.config.host}:{self.config.port}")

        self._server = await websockets.serve(
            self._handle_client,
            self.config.host,
            self.config.port,
        )

        # Keep running until stopped
        while self._running:
            await asyncio.sleep(1)

    async def stop(self) -> None:
        """Stop the WebSocket server."""
        self._running = False
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            logger.info("WebSocket server stopped")

    async def _handle_client(self, websocket: ServerConnection) -> None:
        """Handle a client connection."""
        client_id = str(uuid.uuid4())[:8]

        # Note: We don't authenticate connections because browser WebSockets can't send
        # custom headers. Security is enforced at the message level - the extension
        # validates the token in each request from the server before executing commands.
        # The server only listens on localhost (127.0.0.1) so external connections
        # aren't possible anyway.

        self._clients[client_id] = websocket
        logger.info(f"Client {client_id} connected from {websocket.remote_address}")

        # Send connected event
        await self._send_event(websocket, Event.CONNECTED, {"clientId": client_id})

        try:
            async for message in websocket:
                # Convert bytes to str if needed (JSON messages are text)
                text = message if isinstance(message, str) else message.decode("utf-8")
                await self._handle_message(client_id, websocket, text)
        except websockets.ConnectionClosed:
            logger.info(f"Client {client_id} disconnected")
        finally:
            del self._clients[client_id]

    async def _handle_message(
        self, client_id: str, websocket: ServerConnection, raw: str
    ) -> None:
        """Handle an incoming message from a client."""
        parsed = parse_message(raw)

        if isinstance(parsed, Response):
            # Response to a request we sent
            future = self._pending_requests.pop(parsed.id, None)
            if future and not future.done():
                future.set_result(parsed)
            return

        if isinstance(parsed, Request):
            # Request from extension (queries)
            response = await self._handle_request(parsed)
            await websocket.send(response.to_json())
            return

        logger.warning(f"Unknown message from {client_id}: {raw[:100]}")

    async def _handle_request(self, request: Request) -> Response:
        """Handle a request from the extension."""
        try:
            if request.action == Action.PING.value:
                return Response.success(request.id, {"pong": True})

            elif request.action == "getFolders":
                categories = load_categories(self.categories_file)
                folders = get_category_descriptions(categories)
                return Response.success(request.id, {"folders": folders})

            elif request.action == "getClassifications":
                limit = request.params.get("limit", 50)
                classifications = self._get_recent_classifications(limit)
                return Response.success(request.id, {"classifications": classifications})

            elif request.action == "getStats":
                stats = self.db.get_classification_counts()
                return Response.success(request.id, {"stats": stats})

            else:
                return Response.failure(request.id, f"Unknown action: {request.action}")

        except Exception as e:
            logger.error(f"Error handling request {request.id}: {e}")
            return Response.failure(request.id, str(e))

    def _get_recent_classifications(self, limit: int) -> list[dict]:
        """Get recent classifications from database."""
        emails = self.db.get_recent_classifications(limit)

        return [
            {
                "messageId": email.message_id,
                "subject": email.subject,
                "from": email.from_addr,
                "folder": email.classification,
                "confidence": email.confidence,
                "processedAt": str(email.processed_at) if email.processed_at else None,
            }
            for email in emails
        ]

    async def send_request(
        self,
        action: Action,
        params: dict[str, Any],
        timeout: float = 30.0,
    ) -> Response | None:
        """Send a request to all connected clients and wait for first response."""
        if not self._clients:
            logger.warning("No clients connected")
            return None

        request_id = str(uuid.uuid4())
        request = Request(
            id=request_id,
            action=action.value,
            params=params,
            token=self.config.auth_token or None,
        )

        future: asyncio.Future[Response] = asyncio.get_event_loop().create_future()
        self._pending_requests[request_id] = future

        # Send to first available client
        client_id, websocket = next(iter(self._clients.items()))
        try:
            await websocket.send(request.to_json())
            logger.debug(f"Sent {action.value} request to {client_id}")

            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            logger.warning(f"Request {request_id} timed out")
            self._pending_requests.pop(request_id, None)
            return None
        except Exception as e:
            logger.error(f"Error sending request: {e}")
            self._pending_requests.pop(request_id, None)
            return None

    async def broadcast_event(self, event: Event, data: dict[str, Any]) -> None:
        """Broadcast an event to all connected clients."""
        if not self._clients:
            return

        server_event = ServerEvent(event=event.value, data=data)
        message = server_event.to_json()

        for client_id, websocket in list(self._clients.items()):
            try:
                await websocket.send(message)
            except Exception as e:
                logger.warning(f"Failed to send event to {client_id}: {e}")

    async def _send_event(
        self, websocket: ServerConnection, event: Event, data: dict[str, Any]
    ) -> None:
        """Send an event to a specific client."""
        server_event = ServerEvent(event=event.value, data=data)
        await websocket.send(server_event.to_json())

    @property
    def client_count(self) -> int:
        """Return number of connected clients."""
        return len(self._clients)

    @property
    def is_connected(self) -> bool:
        """Return True if any clients are connected."""
        return len(self._clients) > 0


async def run_websocket_server(config: WebSocketConfig, db: Database, categories_file: str | Path) -> None:
    """Run the WebSocket server (for use in asyncio.gather)."""
    server = WebSocketServer(config, db, categories_file)
    await server.start()
