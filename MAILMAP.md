Folder-Aware MCP Email Classification Architecture (Updated)
1. Core Objectives

Monitor all email folders on a local or hosted IMAP server.

Maintain folder descriptions for classification logic.

Classify incoming emails in real-time, using folder descriptions to guide predictions.

Use local GPU-accelerated LLM via MCP for classification.

Keep system modular, persistent, and robust.

2. Components Overview
Component	Responsibility	Notes
IMAP Listener / Transport Layer	Monitors IMAP server. Uses IDLE for INBOX and other high-priority folders; uses periodic polling for low-traffic folders and new folder detection.	Detects new messages, folder changes, and folder creation.
MCP Client	Wraps email or folder payloads into JSON-RPC calls to the MCP server.	Sends both classify_email and update_folder_descriptions requests.
MCP Server: Folder & Email Classification	Receives MCP requests and invokes local LLM.	Methods: tools/classify_email, tools/update_folder_descriptions.
Local LLM Runtime	Classifies emails and generates folder descriptions.	Ollama / llama.cpp / Qwen 7B or similar on RTX 4070.
Folder Metadata Cache	Maintains folder names, IDs, and LLM-generated descriptions.	Updated periodically or on new folder detection.
Database / Storage	Stores emails, parsed metadata, classification results, folder mappings.	SQLite or lightweight DB.
Optional Action Layer	Applies IMAP flags, moves emails to folders based on predicted categories.	Acts after classification completes.
3. Data Flow
A. Folder Synchronization & Description

Periodically poll IMAP LIST/LSUB to detect:

New folders

Deleted folders

Changes in folder subscriptions

Fetch sample emails from each folder (if new or updated).

MCP client sends folder + sample emails to MCP server.

LLM produces folder descriptions:

{
  "folder_id": "INBOX",
  "description": "Contains personal emails and work notifications."
}


Update folder metadata cache and DB.

B. Email Arrival & Classification

IMAP listener receives new message via:

IDLE for INBOX/high-traffic folders

Polling for low-traffic folders

MCP client sends email + current folder descriptions to MCP server.

LLM classifies email:

{
  "message_id": "abc123",
  "predicted_folder": "Receipts",
  "secondary_labels": ["Shopping", "Finance"],
  "confidence": 0.96
}


Store classification in DB.

Optional: move email to predicted folder or apply flags.

C. Periodic Updates

Folder descriptions refreshed at scheduled intervals (e.g., daily).

Re-classification of older emails if folder semantics change.

4. MCP Method Summary
MCP Method	Purpose	Input	Output
tools/update_folder_descriptions	Generate/update folder summaries	Folder name + sample emails	Folder description JSON
tools/classify_email	Classify a single email	Email content + folder descriptions	Predicted folder, labels, confidence
tools/reclassify_folder (optional)	Re-classify messages after folder updates	Folder ID	Updated classification records
5. Hybrid IMAP Monitoring Strategy
Folder Type	Monitoring Method	Notes
INBOX / high-traffic folders	IDLE	Real-time notifications for new messages
Other low-traffic folders	Periodic polling	Fetch new messages at intervals (minutes)
New folder creation	Periodic polling (LIST/LSUB)	Detect and subscribe automatically

IDLE gives near-instant email notifications.

Polling ensures all folders are tracked and folder descriptions remain accurate.

6. Database / Metadata Layout

Tables:

folders

folder_id, name, description, last_updated

emails

message_id, folder_id, subject, from_addr, body_text, classification, confidence, processed_at

folder_email_map

Tracks classification status relative to folder descriptions

7. System Advantages

Real-time classification for active folders via IDLE.

Folder-aware decision-making improves classification accuracy.

Dynamic adaptation to new folders and folder content changes.

Fully local and GPU-accelerated — no cloud dependencies.

Modular & future-proof — MCP decouples IMAP transport, classification logic, and LLM runtime.