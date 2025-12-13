"""Tests for WebSocket server."""

import asyncio
import contextlib
import json

import pytest
import websockets

from mailmap.categories import Category, save_categories
from mailmap.config import WebSocketConfig
from mailmap.database import Database
from mailmap.protocol import Action, Event, Request, Response, ServerEvent, parse_message
from mailmap.websocket_server import WebSocketServer


class TestProtocol:
    """Tests for protocol message types."""

    def test_request_to_json(self):
        req = Request(id="123", action="ping", params={"foo": "bar"})
        data = json.loads(req.to_json())
        assert data["id"] == "123"
        assert data["action"] == "ping"
        assert data["params"] == {"foo": "bar"}

    def test_request_from_dict(self):
        req = Request.from_dict({"id": "123", "action": "test", "params": {"x": 1}})
        assert req.id == "123"
        assert req.action == "test"
        assert req.params == {"x": 1}

    def test_response_success(self):
        resp = Response.success("123", {"result": "ok"})
        assert resp.id == "123"
        assert resp.ok is True
        assert resp.result == {"result": "ok"}
        assert resp.error is None

    def test_response_failure(self):
        resp = Response.failure("123", "Something went wrong")
        assert resp.id == "123"
        assert resp.ok is False
        assert resp.error == "Something went wrong"

    def test_response_to_json_success(self):
        resp = Response.success("123", {"data": "test"})
        data = json.loads(resp.to_json())
        assert data["id"] == "123"
        assert data["ok"] is True
        assert data["result"] == {"data": "test"}
        assert "error" not in data

    def test_response_to_json_failure(self):
        resp = Response.failure("123", "Error message")
        data = json.loads(resp.to_json())
        assert data["id"] == "123"
        assert data["ok"] is False
        assert data["error"] == "Error message"
        assert "result" not in data

    def test_server_event_to_json(self):
        event = ServerEvent(event="emailClassified", data={"folder": "Inbox"})
        data = json.loads(event.to_json())
        assert data["event"] == "emailClassified"
        assert data["data"] == {"folder": "Inbox"}

    def test_parse_message_request(self):
        raw = '{"id": "1", "action": "ping", "params": {}}'
        msg = parse_message(raw)
        assert isinstance(msg, Request)
        assert msg.action == "ping"

    def test_parse_message_response(self):
        raw = '{"id": "1", "ok": true, "result": {}}'
        msg = parse_message(raw)
        assert isinstance(msg, Response)
        assert msg.ok is True

    def test_parse_message_invalid_json(self):
        msg = parse_message("not json")
        assert msg is None

    def test_parse_message_unknown_format(self):
        msg = parse_message('{"foo": "bar"}')
        assert msg is None


class TestWebSocketServer:
    """Tests for WebSocket server."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create a test database."""
        db_path = tmp_path / "test.db"
        db = Database(str(db_path))
        db.connect()
        db.init_schema()
        yield db
        db.close()

    @pytest.fixture
    def categories_file(self, tmp_path):
        """Create a test categories file."""
        cat_path = tmp_path / "categories.txt"
        # Create an empty categories file
        save_categories([], cat_path)
        return cat_path

    @pytest.fixture
    def config(self):
        """Create test config with random port."""
        import random
        port = random.randint(19000, 19999)
        return WebSocketConfig(enabled=True, host="127.0.0.1", port=port)

    @pytest.fixture
    async def server(self, config, db, categories_file):
        """Create and start a test server."""
        server = WebSocketServer(config, db, categories_file)
        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.2)  # Wait for server to start
        yield server
        await server.stop()
        server_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task

    @pytest.mark.asyncio
    async def test_server_starts(self, config, db, categories_file):
        """Test server starts and accepts connections."""
        server = WebSocketServer(config, db, categories_file)
        server_task = asyncio.create_task(server.start())
        await asyncio.sleep(0.2)

        async with websockets.connect(f"ws://{config.host}:{config.port}") as ws:
            # Should receive connected event
            msg = await asyncio.wait_for(ws.recv(), timeout=2)
            data = json.loads(msg)
            assert data["event"] == "connected"
            assert "clientId" in data["data"]

        await server.stop()
        server_task.cancel()

    @pytest.mark.asyncio
    async def test_client_count(self, server, config):
        """Test client count tracking."""
        assert server.client_count == 0

        async with websockets.connect(f"ws://{config.host}:{config.port}") as ws:
            await ws.recv()  # connected event
            await asyncio.sleep(0.1)
            assert server.client_count == 1

        await asyncio.sleep(0.1)
        assert server.client_count == 0

    @pytest.mark.asyncio
    async def test_ping_action(self, server, config):
        """Test ping action."""
        async with websockets.connect(f"ws://{config.host}:{config.port}") as ws:
            await ws.recv()  # connected event

            await ws.send(json.dumps({
                "id": "test-ping",
                "action": "ping",
                "params": {}
            }))

            msg = await asyncio.wait_for(ws.recv(), timeout=2)
            resp = json.loads(msg)
            assert resp["id"] == "test-ping"
            assert resp["ok"] is True
            assert resp["result"]["pong"] is True

    @pytest.mark.asyncio
    async def test_get_folders_action(self, server, config, categories_file):
        """Test getFolders action."""
        # Add a test category to the file
        categories = [Category(name="TestFolder", description="Test folder")]
        save_categories(categories, categories_file)

        async with websockets.connect(f"ws://{config.host}:{config.port}") as ws:
            await ws.recv()  # connected event

            await ws.send(json.dumps({
                "id": "test-folders",
                "action": "getFolders",
                "params": {}
            }))

            msg = await asyncio.wait_for(ws.recv(), timeout=2)
            resp = json.loads(msg)
            assert resp["id"] == "test-folders"
            assert resp["ok"] is True
            assert "folders" in resp["result"]
            assert "TestFolder" in resp["result"]["folders"]

    @pytest.mark.asyncio
    async def test_get_stats_action(self, server, config):
        """Test getStats action."""
        async with websockets.connect(f"ws://{config.host}:{config.port}") as ws:
            await ws.recv()  # connected event

            await ws.send(json.dumps({
                "id": "test-stats",
                "action": "getStats",
                "params": {}
            }))

            msg = await asyncio.wait_for(ws.recv(), timeout=2)
            resp = json.loads(msg)
            assert resp["id"] == "test-stats"
            assert resp["ok"] is True
            assert "stats" in resp["result"]

    @pytest.mark.asyncio
    async def test_unknown_action(self, server, config):
        """Test unknown action returns error."""
        async with websockets.connect(f"ws://{config.host}:{config.port}") as ws:
            await ws.recv()  # connected event

            await ws.send(json.dumps({
                "id": "test-unknown",
                "action": "unknownAction",
                "params": {}
            }))

            msg = await asyncio.wait_for(ws.recv(), timeout=2)
            resp = json.loads(msg)
            assert resp["id"] == "test-unknown"
            assert resp["ok"] is False
            assert "Unknown action" in resp["error"]

    @pytest.mark.asyncio
    async def test_broadcast_event(self, server, config):
        """Test broadcasting events to multiple clients."""
        async with websockets.connect(f"ws://{config.host}:{config.port}") as ws1:
            await ws1.recv()  # connected event

            async with websockets.connect(f"ws://{config.host}:{config.port}") as ws2:
                await ws2.recv()  # connected event

                # Broadcast event
                await server.broadcast_event(Event.EMAIL_CLASSIFIED, {
                    "messageId": "<test@example.com>",
                    "folder": "TestFolder",
                    "confidence": 0.95
                })

                # Both clients should receive
                msg1 = await asyncio.wait_for(ws1.recv(), timeout=2)
                msg2 = await asyncio.wait_for(ws2.recv(), timeout=2)

                data1 = json.loads(msg1)
                data2 = json.loads(msg2)

                assert data1["event"] == "emailClassified"
                assert data1["data"]["folder"] == "TestFolder"
                assert data2["event"] == "emailClassified"

    @pytest.mark.asyncio
    async def test_send_request_to_client(self, server, config):
        """Test server sending request to client."""
        async with websockets.connect(f"ws://{config.host}:{config.port}") as ws:
            await ws.recv()  # connected event

            # Start server request in background
            async def server_request():
                return await server.send_request(
                    Action.LIST_FOLDERS,
                    {"accountId": "test"},
                    timeout=5
                )

            request_task = asyncio.create_task(server_request())

            # Client receives request and responds
            msg = await asyncio.wait_for(ws.recv(), timeout=2)
            req = json.loads(msg)
            assert req["action"] == "listFolders"

            # Send response
            await ws.send(json.dumps({
                "id": req["id"],
                "ok": True,
                "result": {"folders": ["Inbox", "Sent"]}
            }))

            # Server should receive response
            response = await request_task
            assert response is not None
            assert response.ok is True
            assert response.result["folders"] == ["Inbox", "Sent"]

    @pytest.mark.asyncio
    async def test_send_request_timeout(self, server, config):
        """Test request timeout when client doesn't respond."""
        async with websockets.connect(f"ws://{config.host}:{config.port}") as ws:
            await ws.recv()  # connected event

            # Send request with short timeout, don't respond
            response = await server.send_request(
                Action.PING,
                {},
                timeout=0.5
            )

            assert response is None

    @pytest.mark.asyncio
    async def test_multiple_clients(self, server, config):
        """Test multiple simultaneous clients."""
        clients = []
        for _ in range(3):
            ws = await websockets.connect(f"ws://{config.host}:{config.port}")
            await ws.recv()  # connected event
            clients.append(ws)

        await asyncio.sleep(0.1)
        assert server.client_count == 3

        # Each client can send requests
        for i, ws in enumerate(clients):
            await ws.send(json.dumps({
                "id": f"ping-{i}",
                "action": "ping",
                "params": {}
            }))
            msg = await asyncio.wait_for(ws.recv(), timeout=2)
            resp = json.loads(msg)
            assert resp["ok"] is True

        for ws in clients:
            await ws.close()

    @pytest.mark.asyncio
    async def test_is_connected_property(self, server, config):
        """Test is_connected property."""
        assert server.is_connected is False

        async with websockets.connect(f"ws://{config.host}:{config.port}") as ws:
            await ws.recv()
            await asyncio.sleep(0.1)
            assert server.is_connected is True

        await asyncio.sleep(0.1)
        assert server.is_connected is False
