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
      const account = await resolveAccount(accountId);
      if (!account) {
        throw new Error(`Account not found: ${accountId}`);
      }
      return { folders: flattenFolders(account.folders, account.id) };
    }

    // Get folders for all accounts
    const accounts = await browser.accounts.list();
    const allFolders = [];
    for (const account of accounts) {
      allFolders.push(...flattenFolders(account.folders, account.id));
    }
    return { folders: allFolders };
  },

  listMessages: async (params) => {
    const { accountId, path, limit = 10 } = params;

    // Find the folder
    const account = await browser.accounts.get(accountId);
    if (!account) {
      throw new Error(`Account not found: ${accountId}`);
    }

    const folder = findFolderInTree(account.folders, path);
    if (!folder) {
      throw new Error(`Folder not found: ${path}`);
    }

    // Query messages in folder
    const page = await browser.messages.list(folder);
    const messages = [];

    for (const msg of page.messages.slice(0, limit)) {
      messages.push({
        headerMessageId: msg.headerMessageId,
        subject: msg.subject,
        author: msg.author,
        date: msg.date,
      });
    }

    return { messages };
  },

  createFolder: async (params) => {
    const { accountId, parentPath, name } = params;

    const account = await browser.accounts.get(accountId);
    if (!account) {
      throw new Error(`Account not found: ${accountId}`);
    }

    let parentFolder;
    if (parentPath) {
      parentFolder = findFolderInTree(account.folders, parentPath);
      if (!parentFolder) {
        throw new Error(`Parent folder not found: ${parentPath}`);
      }
    } else {
      // Create at root level - use account's root folders
      parentFolder = account;
    }

    const newFolder = await browser.folders.create(parentFolder, name);
    return {
      path: newFolder.path,
      name: newFolder.name,
    };
  },

  renameFolder: async (params) => {
    const { accountId, path, newName } = params;

    const account = await browser.accounts.get(accountId);
    if (!account) {
      throw new Error(`Account not found: ${accountId}`);
    }

    const folder = findFolderInTree(account.folders, path);
    if (!folder) {
      throw new Error(`Folder not found: ${path}`);
    }

    const renamed = await browser.folders.rename(folder, newName);
    return {
      path: renamed.path,
      name: renamed.name,
    };
  },

  deleteFolder: async (params) => {
    const { accountId, path } = params;

    const account = await resolveAccount(accountId);
    if (!account) {
      throw new Error(`Account not found: ${accountId}`);
    }

    const folder = findFolderInTree(account.folders, path);
    if (!folder) {
      throw new Error(`Folder not found: ${path}`);
    }

    await browser.folders.delete(folder);
    return { deleted: path };
  },

  getMessage: async (params) => {
    const { headerMessageId } = params;

    // Resolve header Message-ID to Thunderbird internal ID
    const result = await browser.messages.query({ headerMessageId });
    if (result.messages.length === 0) {
      throw new Error(`Message not found: ${headerMessageId}`);
    }

    const message = result.messages[0];

    // Get full message content
    const full = await browser.messages.getFull(message.id);

    return {
      headerMessageId,
      subject: message.subject,
      author: message.author,
      date: message.date,
      folder: message.folder,
      body: extractBody(full),
    };
  },

  moveMessages: async (params) => {
    const { headerMessageIds, targetFolder } = params;

    if (!headerMessageIds || !headerMessageIds.length) {
      throw new Error("No header message IDs provided");
    }
    if (!targetFolder) {
      throw new Error("No target folder provided");
    }

    // Find or create the target folder
    const folder = await findOrCreateFolder(targetFolder);

    // Resolve header Message-IDs to Thunderbird internal IDs
    const { tbIds, notFound } = await resolveHeaderMessageIds(headerMessageIds);

    if (tbIds.length === 0) {
      return { moved: 0, notFound, targetFolder: folder.path };
    }

    // Move messages
    await browser.messages.move(tbIds, folder);

    return {
      moved: tbIds.length,
      notFound,
      targetFolder: folder.path,
    };
  },

  copyMessages: async (params) => {
    const { headerMessageIds, targetFolder } = params;

    if (!headerMessageIds || !headerMessageIds.length) {
      throw new Error("No header message IDs provided");
    }
    if (!targetFolder) {
      throw new Error("No target folder provided");
    }

    // Find or create the target folder
    const folder = await findOrCreateFolder(targetFolder);

    // Resolve header Message-IDs to Thunderbird internal IDs
    const { tbIds, notFound } = await resolveHeaderMessageIds(headerMessageIds);

    if (tbIds.length === 0) {
      return { copied: 0, notFound, targetFolder: folder.path };
    }

    await browser.messages.copy(tbIds, folder);

    return {
      copied: tbIds.length,
      notFound,
      targetFolder: folder.path,
    };
  },

  tagMessages: async (params) => {
    const { headerMessageIds, tags } = params;

    if (!headerMessageIds || !headerMessageIds.length) {
      throw new Error("No header message IDs provided");
    }

    // Resolve header Message-IDs to Thunderbird internal IDs
    const { tbIds, notFound } = await resolveHeaderMessageIds(headerMessageIds);

    for (const tbId of tbIds) {
      await browser.messages.update(tbId, { tags });
    }

    return {
      tagged: tbIds.length,
      notFound,
      tags,
    };
  },

  deleteMessages: async (params) => {
    const { headerMessageIds, permanently } = params;

    if (!headerMessageIds || !headerMessageIds.length) {
      throw new Error("No header message IDs provided");
    }

    // Resolve header Message-IDs to Thunderbird internal IDs
    const { tbIds, notFound } = await resolveHeaderMessageIds(headerMessageIds);

    if (tbIds.length > 0) {
      await browser.messages.delete(tbIds, permanently || false);
    }

    return {
      deleted: tbIds.length,
      notFound,
    };
  },
};

// Helper: Resolve RFC 2822 Message-ID headers to Thunderbird internal IDs
async function resolveHeaderMessageIds(headerMessageIds) {
  const tbIds = [];
  const notFound = [];

  for (const headerMsgId of headerMessageIds) {
    // Try with original format first
    let result = await browser.messages.query({ headerMessageId: headerMsgId });

    // If not found and has angle brackets, try without them
    if (result.messages.length === 0 && headerMsgId.startsWith('<') && headerMsgId.endsWith('>')) {
      const stripped = headerMsgId.slice(1, -1);
      result = await browser.messages.query({ headerMessageId: stripped });
      if (result.messages.length > 0) {
        console.log(`[mailmap] Found with stripped brackets: ${stripped}`);
      }
    }

    // If still not found, log for debugging
    if (result.messages.length === 0) {
      console.log(`[mailmap] Message not found: ${headerMsgId}`);
    }

    if (result.messages.length > 0) {
      tbIds.push(result.messages[0].id);
    } else {
      notFound.push(headerMsgId);
    }
  }

  return { tbIds, notFound };
}

// Helper: Resolve account by ID or alias ("local", "imap")
async function resolveAccount(accountId) {
  const accounts = await browser.accounts.list();
  if (accountId === "local") {
    return accounts.find(a => a.type === "none");
  } else if (accountId === "imap") {
    return accounts.find(a => a.type === "imap");
  } else if (accountId) {
    return browser.accounts.get(accountId);
  }
  // Default to Local Folders
  return accounts.find(a => a.type === "none") || accounts[0];
}

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

// Helper: Find folder or create it if it doesn't exist
async function findOrCreateFolder(spec) {
  const accountId = spec.accountId;
  const path = spec.path || spec;

  const targetAccount = await resolveAccount(accountId);
  if (!targetAccount) {
    throw new Error("No target account found");
  }

  // Search ONLY in target account
  let folder = findFolderInTree(targetAccount.folders, path);
  if (folder) {
    return folder;
  }

  // Folder doesn't exist in target account - create it
  console.log(`[mailmap] Creating folder '${path}' in: ${targetAccount.name} (${targetAccount.type})`)

  // Create folder(s) - handle nested paths like "Parent/Child"
  const parts = path.split("/");
  let parentFolder = targetAccount;

  for (let i = 0; i < parts.length; i++) {
    const folderName = parts[i];
    const existingFolder = findFolderInTree(
      parentFolder.folders || parentFolder.subFolders || [],
      folderName
    );

    if (existingFolder) {
      parentFolder = existingFolder;
    } else {
      // Create this folder
      console.log(`[mailmap] Creating folder: ${folderName} in ${parentFolder.name || targetAccount.name}`);
      const newFolder = await browser.folders.create(parentFolder, folderName);
      parentFolder = newFolder;
    }
  }

  return parentFolder;
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
  console.log("[mailmap] Client ID:", data.clientId);
});

mailmap.on("emailClassified", (data) => {
  console.log("[mailmap] Classified:", data.messageId, "->", data.folder);
});

mailmap.on("batchComplete", (data) => {
  console.log("[mailmap] Batch complete:", data.count, "emails");
});

// Start connection
mailmap.connect();
