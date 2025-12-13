/**
 * Mailmap Thunderbird Extension - Background Script
 *
 * Handles commands from mailmap WebSocket server and executes
 * Thunderbird MailExtension API calls.
 */

// Action handlers
const actionHandlers = {
  ping: async () => {
    return { pong: true, timestamp: Date.now() };
  },

  listAccounts: async () => {
    const accounts = await browser.accounts.list();
    return {
      accounts: accounts.map(acc => ({
        id: acc.id,
        name: acc.name,
        type: acc.type,
      })),
    };
  },

  listFolders: async (params) => {
    const { accountId } = params;

    if (accountId) {
      // Get folders for specific account
      const account = await browser.accounts.get(accountId);
      if (!account) {
        throw new Error(`Account not found: ${accountId}`);
      }
      return { folders: flattenFolders(account.folders, accountId) };
    }

    // Get folders for all accounts
    const accounts = await browser.accounts.list();
    const allFolders = [];
    for (const account of accounts) {
      allFolders.push(...flattenFolders(account.folders, account.id));
    }
    return { folders: allFolders };
  },

  getMessage: async (params) => {
    const { messageId } = params;
    const message = await browser.messages.get(messageId);
    if (!message) {
      throw new Error(`Message not found: ${messageId}`);
    }

    // Get full message content
    const full = await browser.messages.getFull(messageId);

    return {
      id: message.id,
      subject: message.subject,
      author: message.author,
      date: message.date,
      folder: message.folder,
      body: extractBody(full),
    };
  },

  moveMessages: async (params) => {
    const { messageIds, targetFolder } = params;

    if (!messageIds || !messageIds.length) {
      throw new Error("No message IDs provided");
    }
    if (!targetFolder) {
      throw new Error("No target folder provided");
    }

    // Find the target folder
    const folder = await findFolder(targetFolder);
    if (!folder) {
      throw new Error(`Target folder not found: ${JSON.stringify(targetFolder)}`);
    }

    // Move messages
    await browser.messages.move(messageIds, folder);

    return {
      moved: messageIds.length,
      targetFolder: folder.path,
    };
  },

  copyMessages: async (params) => {
    const { messageIds, targetFolder } = params;

    if (!messageIds || !messageIds.length) {
      throw new Error("No message IDs provided");
    }
    if (!targetFolder) {
      throw new Error("No target folder provided");
    }

    const folder = await findFolder(targetFolder);
    if (!folder) {
      throw new Error(`Target folder not found: ${JSON.stringify(targetFolder)}`);
    }

    await browser.messages.copy(messageIds, folder);

    return {
      copied: messageIds.length,
      targetFolder: folder.path,
    };
  },

  tagMessages: async (params) => {
    const { messageIds, tags } = params;

    if (!messageIds || !messageIds.length) {
      throw new Error("No message IDs provided");
    }

    for (const messageId of messageIds) {
      await browser.messages.update(messageId, { tags });
    }

    return {
      tagged: messageIds.length,
      tags,
    };
  },

  deleteMessages: async (params) => {
    const { messageIds, permanently } = params;

    if (!messageIds || !messageIds.length) {
      throw new Error("No message IDs provided");
    }

    await browser.messages.delete(messageIds, permanently || false);

    return {
      deleted: messageIds.length,
    };
  },
};

// Helper: Flatten nested folder structure
function flattenFolders(folders, accountId, parentPath = "") {
  const result = [];
  for (const folder of folders || []) {
    const path = parentPath ? `${parentPath}/${folder.name}` : folder.name;
    result.push({
      accountId,
      path,
      name: folder.name,
      type: folder.type,
    });
    if (folder.subFolders && folder.subFolders.length) {
      result.push(...flattenFolders(folder.subFolders, accountId, path));
    }
  }
  return result;
}

// Helper: Find folder by path or specification
async function findFolder(spec) {
  // spec can be: { accountId, path } or just a path string
  const accountId = spec.accountId;
  const path = spec.path || spec;

  if (accountId) {
    const account = await browser.accounts.get(accountId);
    if (account) {
      return findFolderInTree(account.folders, path);
    }
  }

  // Search all accounts
  const accounts = await browser.accounts.list();
  for (const account of accounts) {
    const folder = findFolderInTree(account.folders, path);
    if (folder) {
      return folder;
    }
  }

  return null;
}

// Helper: Find folder in tree by path
function findFolderInTree(folders, path) {
  const parts = path.split("/");
  let current = folders;

  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    const found = (current || []).find(f => f.name === part);
    if (!found) {
      return null;
    }
    if (i === parts.length - 1) {
      return found;
    }
    current = found.subFolders;
  }

  return null;
}

// Helper: Extract body text from full message
function extractBody(fullMessage) {
  const parts = fullMessage.parts || [];

  // Look for text/plain first
  for (const part of parts) {
    if (part.contentType === "text/plain" && part.body) {
      return part.body;
    }
  }

  // Fall back to text/html (strip tags)
  for (const part of parts) {
    if (part.contentType === "text/html" && part.body) {
      return part.body.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
    }
  }

  // Recurse into nested parts
  for (const part of parts) {
    if (part.parts) {
      const body = extractBody(part);
      if (body) {
        return body;
      }
    }
  }

  return "";
}

// Override executeAction in protocol.js
mailmap.executeAction = async (action, params) => {
  const handler = actionHandlers[action];
  if (!handler) {
    throw new Error(`Unknown action: ${action}`);
  }
  return await handler(params);
};

// Handle server events
mailmap.on("connected", (data) => {
  console.log("[mailmap] Server assigned client ID:", data.clientId);
});

mailmap.on("emailClassified", (data) => {
  console.log("[mailmap] Email classified:", data.messageId, "->", data.folder);
  // Could show notification or update UI here
});

mailmap.on("batchComplete", (data) => {
  console.log("[mailmap] Batch complete:", data);
});

// Start connection
console.log("[mailmap] Extension starting...");
mailmap.connect();
