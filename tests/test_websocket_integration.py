"""Integration tests for WebSocket server with Thunderbird extension.

These tests require:
1. MAILMAP_WS_TOKEN environment variable set
2. Thunderbird running with the mailmap extension
3. Extension configured with matching token

Run with: pytest tests/test_websocket_integration.py -v -s

Note: Extension has a 5-second reconnect delay, so tests wait up to 10 seconds
for connection.
"""

import asyncio
import contextlib
import os
import tempfile

import pytest

from mailmap.config import WebSocketConfig
from mailmap.database import Database
from mailmap.protocol import Action
from mailmap.websocket_server import WebSocketServer

# Skip all tests if token not configured
pytestmark = pytest.mark.skipif(
    not os.environ.get("MAILMAP_WS_TOKEN"),
    reason="MAILMAP_WS_TOKEN not set - skipping WebSocket integration tests"
)


@pytest.mark.asyncio
async def test_websocket_extension_integration():
    """Test WebSocket communication with Thunderbird extension.

    This test:
    1. Starts a WebSocket server with auth token
    2. Waits for extension to connect
    3. Sends ping command (validates token auth)
    4. Lists folders from Thunderbird
    5. Lists accounts from Thunderbird
    """
    ws_token = os.environ.get("MAILMAP_WS_TOKEN")
    assert ws_token, "MAILMAP_WS_TOKEN must be set"

    # Create temp database
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    db = Database(db_path)
    db.connect()
    db.init_schema()

    # Create temp categories file
    cat_path = tempfile.mktemp(suffix=".txt")
    with open(cat_path, "w") as f:
        f.write("Test: Test category\n")

    config = WebSocketConfig(
        enabled=True,
        host="127.0.0.1",
        port=9753,
        auth_token=ws_token
    )

    server = WebSocketServer(config, db, cat_path)
    server_task = asyncio.create_task(server.start())

    try:
        # Wait for server to start and extension to connect (up to 10 seconds)
        print("\nWaiting for extension to connect (up to 10 seconds)...")
        for i in range(20):
            await asyncio.sleep(0.5)
            if server.is_connected:
                print(f"Extension connected after {(i+1)*0.5:.1f}s")
                break
        else:
            pytest.fail(
                "Extension did not connect within 10 seconds. "
                "Is Thunderbird running with the extension?"
            )

        # Test 1: Ping (validates token authentication)
        print("Testing PING with token auth...")
        response = await server.send_request(Action.PING, {}, timeout=5)
        assert response is not None, "No response from extension"
        assert response.ok is True, f"Ping failed: {response.error}"
        print("  PING: OK")

        # Test 2: List folders
        print("Testing LIST_FOLDERS...")
        response = await server.send_request(Action.LIST_FOLDERS, {}, timeout=10)
        assert response is not None, "No response from extension"
        assert response.ok is True, f"List folders failed: {response.error}"
        assert "folders" in response.result
        folders = response.result["folders"]
        assert len(folders) > 0, "No folders returned"
        print(f"  LIST_FOLDERS: OK ({len(folders)} folders)")

        # Test 3: List accounts
        print("Testing LIST_ACCOUNTS...")
        response = await server.send_request(Action.LIST_ACCOUNTS, {}, timeout=5)
        assert response is not None, "No response from extension"
        assert response.ok is True, f"List accounts failed: {response.error}"
        assert "accounts" in response.result
        accounts = response.result["accounts"]
        print(f"  LIST_ACCOUNTS: OK ({len(accounts)} accounts)")

        print("\nAll integration tests passed!")

    finally:
        # Cleanup
        await server.stop()
        server_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await server_task
        db.close()
        os.unlink(db_path)
        os.unlink(cat_path)
