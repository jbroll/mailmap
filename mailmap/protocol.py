"""WebSocket protocol definitions for mailmap <-> Thunderbird MailExtension communication."""

import json
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


class Action(str, Enum):
    """Actions that mailmap can request from the extension."""
    MOVE_MESSAGES = "moveMessages"
    COPY_MESSAGES = "copyMessages"
    DELETE_MESSAGES = "deleteMessages"
    LIST_FOLDERS = "listFolders"
    LIST_ACCOUNTS = "listAccounts"
    GET_MESSAGE = "getMessage"
    TAG_MESSAGES = "tagMessages"
    CREATE_FOLDER = "createFolder"
    RENAME_FOLDER = "renameFolder"
    DELETE_FOLDER = "deleteFolder"
    PING = "ping"


class Event(str, Enum):
    """Events that mailmap pushes to extensions."""
    EMAIL_CLASSIFIED = "emailClassified"
    FOLDER_UPDATED = "folderUpdated"
    BATCH_COMPLETE = "batchComplete"
    CONNECTED = "connected"


@dataclass
class Request:
    """Request from mailmap to extension."""
    id: str
    action: str
    params: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_dict(cls, data: dict) -> "Request":
        return cls(
            id=data["id"],
            action=data["action"],
            params=data.get("params", {}),
        )


@dataclass
class Response:
    """Response from extension to mailmap."""
    id: str
    ok: bool
    result: dict[str, Any] | None = None
    error: str | None = None

    def to_json(self) -> str:
        d = {"id": self.id, "ok": self.ok}
        if self.ok:
            d["result"] = self.result or {}
        else:
            d["error"] = self.error or "Unknown error"
        return json.dumps(d)

    @classmethod
    def from_dict(cls, data: dict) -> "Response":
        return cls(
            id=data["id"],
            ok=data["ok"],
            result=data.get("result"),
            error=data.get("error"),
        )

    @classmethod
    def success(cls, request_id: str, result: dict[str, Any] | None = None) -> "Response":
        return cls(id=request_id, ok=True, result=result)

    @classmethod
    def failure(cls, request_id: str, error: str) -> "Response":
        return cls(id=request_id, ok=False, error=error)


@dataclass
class ServerEvent:
    """Server-initiated event pushed to extensions."""
    event: str
    data: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def from_dict(cls, data: dict) -> "ServerEvent":
        return cls(
            event=data["event"],
            data=data.get("data", {}),
        )


def parse_message(raw: str) -> Request | Response | None:
    """Parse a JSON message into Request or Response."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if "action" in data:
        return Request.from_dict(data)
    elif "ok" in data:
        return Response.from_dict(data)
    return None
