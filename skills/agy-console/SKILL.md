---
name: agy-console
description: Collaborate with Antigravity through a running local Agy Console, preserving the current Codex chat binding, model preferences, task results and review decisions. Use when the user requests Ag collaboration or has enabled it for substantive project work.
---

# Codex and Agy Console

Use the installed dispatcher command in the Local connection section below. The package installer creates that section and `connection.json`; they describe this installation, without credentials. If they are absent, install the companion skill using the console package's `scripts/install_codex_skill.py` (Windows: `Install Codex Skill.cmd`). Do not guess another installation or chat.

Read `CODEX_THREAD_ID` from the current environment. Start substantive work with the dispatcher arguments `--ensure --title "Brief task title"`; this binds or retrieves the current chat without calling a model. Respect `collaboration_enabled:false` and any user request for Codex only. Normal binds must preserve saved collaboration, model and automatic-page preferences.

When collaboration is enabled, submit a bounded contribution early enough to inform the current work. Include only relevant material; do not send credentials, unrelated private files or complete chat transcripts. Codex performs the work and verification. Short replies and trivial edits need no model call.

Write a focused task file and optional context excerpts outside the public source, then append these arguments to the dispatcher command:

```text
--task-file /absolute/task.txt --context /absolute/relevant.txt --out /absolute/new-review-directory --timeout 600
```

Omit `--model` for normal dispatch: the saved text/image model and fallback order are user choices. Continue the primary Ag conversation across follow-ups. Use a stable `--branch` only for an explicitly separate work context. Do not create standalone sessions or use a globally recent conversation as this chat's identity.

When `panel_action.enabled` or `auto_open_activity` is true, reveal the returned current-chat URL in the existing right browser panel before waiting. Reuse its provider tab ID through `open_in_codex` when available; otherwise use supported browser visibility controls. Navigate only if the URL differs. A queued open is pending, not proof of visibility. If tools cannot reveal the page, disclose that limitation and provide the exact fixed URL. When automatic opening is false, preserve it. Never use the shared root URL as a substitute for a bound chat.

The CLI shares its official local sign-in. If login expires, let the user sign in through the official flow; do not read or transfer authentication files. If the foreground console is stopped, ask the user to open its normal launcher. Do not start a hidden service. Terminal closure remains authoritative. A bridge timeout can leave an active task: inspect the returned task URL before submitting again.

Inspect the actual result: `completed:true`, completed task status, nonempty reply and the expected persistent conversation ID. Denied tools or missing output cannot be counted as success. A saved collaboration toggle or an opened page does not mean a model task ran. A quota diagnostic alone does not authorize replay: preserve the console's current-turn evidence and single-fallback limits. Never buy credits, change accounts or retry indefinitely.

Integrate useful findings and record actual accepted, rejected or deferred decisions through `--review-turn ACTUAL_TURN_ID --review-file /absolute/review.json` when supported. The JSON needs `summary`, `items` with `suggestion`, `decision`, `reason`, and `verification` with `status` (`passed`, `partial` or `not_run`) and `details`. This records decisions without calling a model. Missing service support means recording is pending. Reuse the existing contribution; make another request only for a concrete unresolved issue or material change.

This skill grants no permissions beyond the user's task. It does not authorize contacting people or other chats, changing approvals, uploading private state, or promising that opening a page wakes an idle Codex conversation.
