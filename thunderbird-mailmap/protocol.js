/**
 * WebSocket connection management for mailmap.
 */

const MAILMAP_WS_URL = "ws://127.0.0.1:9753";
const RECONNECT_DELAY = 5000; // Retry every 5 seconds

class MailmapConnection {
  constructor() {
    this.ws = null;
    this.pendingRequests = new Map(); // id -> {resolve, reject, timeout}
    this.eventHandlers = new Map(); // event -> [handlers]
    this.connected = false;
    this.reconnectScheduled = false; // Prevent double scheduling
    this.authToken = null; // Token for authenticating server requests
    this.loadAuthToken();
  }

  async loadAuthToken() {
    try {
      const result = await browser.storage.local.get("authToken");
      this.authToken = result.authToken || null;
      console.log("[mailmap] Auth token loaded:", this.authToken ? "configured" : "not set");
    } catch (e) {
      console.error("[mailmap] Failed to load auth token:", e);
    }

    // Listen for token changes
    browser.storage.onChanged.addListener((changes, area) => {
      if (area === "local" && changes.authToken) {
        this.authToken = changes.authToken.newValue || null;
        console.log("[mailmap] Auth token updated:", this.authToken ? "configured" : "cleared");
      }
    });
  }

  connect() {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      return;
    }

    this.reconnectScheduled = false; // Clear flag when actually connecting
    this.ws = new WebSocket(MAILMAP_WS_URL);

    this.ws.onopen = () => {
      console.log("[mailmap] Connected to", MAILMAP_WS_URL);
      this.connected = true;
    };

    this.ws.onclose = () => {
      this.connected = false;
      this.scheduleReconnect();
    };

    this.ws.onerror = (event) => {
      // Connection errors before open won't trigger onclose
      // Schedule reconnect if we're not connected yet
      if (!this.connected) {
        this.scheduleReconnect();
      }
    };

    this.ws.onmessage = (event) => {
      this.handleMessage(event.data);
    };
  }

  scheduleReconnect() {
    // Prevent double scheduling from onerror + onclose both firing
    if (this.reconnectScheduled) {
      return;
    }
    this.reconnectScheduled = true;
    setTimeout(() => this.connect(), RECONNECT_DELAY);
  }

  handleMessage(raw) {
    let msg;
    try {
      msg = JSON.parse(raw);
    } catch (e) {
      console.error("[mailmap] Invalid JSON:", raw);
      return;
    }

    // Server event (push from mailmap)
    if (msg.event) {
      this.dispatchEvent(msg.event, msg.data);
      return;
    }

    // Request from server (mailmap asking extension to do something)
    if (msg.action) {
      this.handleRequest(msg);
      return;
    }

    // Response to our request (shouldn't happen in this architecture)
    if (msg.id && typeof msg.ok !== "undefined") {
      const pending = this.pendingRequests.get(msg.id);
      if (pending) {
        clearTimeout(pending.timeout);
        this.pendingRequests.delete(msg.id);
        if (msg.ok) {
          pending.resolve(msg.result);
        } else {
          pending.reject(new Error(msg.error || "Request failed"));
        }
      }
      return;
    }

    console.warn("[mailmap] Unknown message:", msg);
  }

  async handleRequest(request) {
    const { id, action, params, token } = request;
    let result;
    let error = null;

    console.log(`[mailmap] <- ${action}`, params);

    // Validate token - reject if no token configured (secure by default)
    if (!this.authToken) {
      console.warn(`[mailmap] Rejected ${action}: no auth token configured in extension`);
      this.send({ id, ok: false, error: "Authentication failed: extension has no token configured" });
      return;
    }
    if (!token || token !== this.authToken) {
      console.warn(`[mailmap] Rejected ${action}: invalid or missing token`);
      this.send({ id, ok: false, error: "Authentication failed: invalid token" });
      return;
    }

    try {
      result = await this.executeAction(action, params || {});
      console.log(`[mailmap] -> ${action} OK`);
    } catch (e) {
      error = e.message || String(e);
      console.error(`[mailmap] -> ${action} FAILED:`, error);
    }

    // Send response
    const response = error
      ? { id, ok: false, error }
      : { id, ok: true, result: result || {} };

    this.send(response);
  }

  async executeAction(action, params) {
    // This will be overridden by background.js
    throw new Error(`Unknown action: ${action}`);
  }

  send(data) {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return false;
    }
    this.ws.send(JSON.stringify(data));
    return true;
  }

  sendRequest(action, params = {}, timeout = 30000) {
    return new Promise((resolve, reject) => {
      const id = crypto.randomUUID();
      const timeoutId = setTimeout(() => {
        this.pendingRequests.delete(id);
        reject(new Error(`Request ${action} timed out`));
      }, timeout);

      this.pendingRequests.set(id, { resolve, reject, timeout: timeoutId });
      this.send({ id, action, params });
    });
  }

  on(event, handler) {
    if (!this.eventHandlers.has(event)) {
      this.eventHandlers.set(event, []);
    }
    this.eventHandlers.get(event).push(handler);
  }

  off(event, handler) {
    const handlers = this.eventHandlers.get(event);
    if (handlers) {
      const index = handlers.indexOf(handler);
      if (index >= 0) {
        handlers.splice(index, 1);
      }
    }
  }

  dispatchEvent(event, data) {
    const handlers = this.eventHandlers.get(event) || [];
    for (const handler of handlers) {
      try {
        handler(data);
      } catch (e) {
        console.error(`[mailmap] Event handler error for ${event}:`, e);
      }
    }
  }
}

// Global connection instance
const mailmap = new MailmapConnection();
