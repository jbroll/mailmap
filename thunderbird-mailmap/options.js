/**
 * Options page for Mailmap Connector
 */

const tokenInput = document.getElementById("token");
const generateBtn = document.getElementById("generate");
const saveBtn = document.getElementById("save");
const statusDiv = document.getElementById("status");

// Load saved token on page load
async function loadToken() {
  try {
    const result = await browser.storage.local.get("authToken");
    if (result.authToken) {
      tokenInput.value = result.authToken;
    }
  } catch (e) {
    console.error("Failed to load token:", e);
  }
}

// Generate a random token
function generateToken() {
  const array = new Uint8Array(24);
  crypto.getRandomValues(array);
  return Array.from(array, b => b.toString(16).padStart(2, "0")).join("");
}

// Show status message
function showStatus(message, isError = false) {
  statusDiv.textContent = message;
  statusDiv.className = "status " + (isError ? "error" : "success");
  setTimeout(() => {
    statusDiv.className = "status";
  }, 3000);
}

// Save token
async function saveToken() {
  const token = tokenInput.value.trim();
  try {
    await browser.storage.local.set({ authToken: token });
    if (token) {
      showStatus("Token saved! Set MAILMAP_WS_TOKEN to the same value when running mailmap.");
    } else {
      showStatus("Token cleared. All commands will be rejected until a token is set.", true);
    }
  } catch (e) {
    console.error("Failed to save token:", e);
    showStatus("Failed to save token: " + e.message, true);
  }
}

// Event handlers
generateBtn.addEventListener("click", () => {
  tokenInput.value = generateToken();
});

saveBtn.addEventListener("click", saveToken);

// Allow saving with Enter key
tokenInput.addEventListener("keypress", (e) => {
  if (e.key === "Enter") {
    saveToken();
  }
});

// Load on startup
loadToken();
