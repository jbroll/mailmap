Below is a concise, implementation-ready design plan for the socket-based Thunderbird control bus, with minimal moving parts and clear responsibilities.

1. Design goal (explicit)

Allow one or more external processes to control Thunderbird mail

Use a local socket as the control interface

Avoid touching Thunderbird storage

Remain fully supported and upgrade-safe

2. High-level architecture
External Clients
    ⇄  Local Socket (TCP or Unix)
Socket Bridge (native host)
    ⇄  Native Messaging (stdin/stdout)
Thunderbird MailExtension
    ⇄  browser.messages / browser.folders
Local Mail


Key principle:

All mail mutation happens inside Thunderbird.
All networking happens outside Thunderbird.

3. Components and responsibilities
A. Thunderbird MailExtension (shim)

Responsibilities

Translate JSON commands → MailExtension API calls

Return structured results/errors

No business logic

No networking

No persistence

Characteristics

~150–300 LOC

Background script only

Reconnect-safe

APIs exposed

listAccounts

listFolders

listMessages

getMessage

moveMessages

copyMessages

tagMessages

deleteMessages

B. Native Messaging Host (socket bridge)

Responsibilities

Act as protocol adapter

Maintain socket server

Forward requests/responses

Correlate requests (requestId)

Handle multiple clients (optional)

Reads

stdin (from Thunderbird)

Writes

stdout (to Thunderbird)

Also

Reads/writes socket(s)

This is the only component that:

opens ports

accepts connections

multiplexes clients

C. External clients

Responsibilities

Classification

Automation

CLI / UI / agents

No Thunderbird knowledge required

Contract

JSON over socket

Stable schema

4. Transport choice
Recommended default

Unix domain socket (Linux/macOS)

No ports

Permission-controlled

Lower overhead

Acceptable alternative

TCP 127.0.0.1:PORT

Easier cross-platform

Add authentication token

5. Message protocol (simple and sufficient)
Request
{
  "id": "uuid-or-int",
  "action": "moveMessages",
  "params": {
    "messageIds": [123, 456],
    "targetFolder": {
      "accountId": "local",
      "path": "Archive/2024"
    }
  }
}

Response
{
  "id": "same-id",
  "ok": true,
  "result": {}
}

Error
{
  "id": "same-id",
  "ok": false,
  "error": "Permission denied"
}


Rules:

Extension is stateless

Bridge handles correlation

Clients never talk native messaging directly

6. Lifecycle and restart behavior
Action	Effect
Reload extension	Host is terminated and restarted
Kill host	Extension reconnects automatically
Client disconnects	No impact on others
Thunderbird exits	Host exits (EOF on stdin)
7. Security boundaries

Socket bound to localhost or filesystem

Optional shared secret on connect

Thunderbird enforces:

folder permissions

account boundaries

No direct filesystem access to mail

8. Failure handling

Native host crash → extension reconnects

Client crash → socket cleanup only

Extension reload → clean state

Mail corruption risk: none

9. Development order (recommended)

Implement MailExtension shim

Implement native host without sockets (echo test)

Add socket server

Add request correlation

Add auth / ACLs if needed

Add multi-client support if needed

10. Why this design is “right sized”

Minimal Thunderbird code

Clear ownership of complexity

Easy debugging and restart

Extensible without redesign

Uses only supported APIs