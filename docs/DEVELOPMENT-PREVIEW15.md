# preview.15 source provenance and scope

The received 197-file source freeze merges reviewed preview.14/.15 application changes into the frozen preview.13 distribution candidate, whose public baseline commit is `69e00ab18bf6c55ab096c5f9c52a4edac091cae4`. It is a developer handoff, not a Release or a replacement for the publisher's latest branch. The publisher merges the current public preview.13 changes and the matching public Skill copy. Final exact-commit CI and performed installation checks are recorded in the Release; real Windows/Linux login and model acceptance remain unverified. Historical preview.8 release links and preview.13 documentation remain historical.

## Independent roundtable meetings

Meetings have their own stable ID, exact Codex chat and work-branch scope, frozen host/participants, proposal/review rounds and host summary. Actual browser contributions can be recorded before an Agy task exists; Agy slots link to real official CLI turns. Source IDs deduplicate meeting creation and observed events. Repeated dispatch of the same native meeting stage queues once. Stage barriers, missing participants, failed/cancelled slots and unknown send outcomes remain explicit; a partially completed meeting never claims consensus. Pauses prevent new work and retain already observed results. Meetings persist through save, restart and backup/restore.

`bridge.py --meeting-create file.json`, `--meeting-event file.json` and `--meeting-view ID` (or `list`) operate on the current real `CODEX_THREAD_ID`; JSON must explicitly match that chat. A native task uses `--meeting ID --meeting-stage proposal|review|summary`. Recording/viewing does not itself call a model or start a browser. See argument help and isolated `test_roundtable_meetings.py`, `test_roundtable_meeting_bridge.py` and `test_roundtable_meeting_http.py` for the exact request contracts and synthetic fixtures. Never replace a pending/unknown browser submission with a blind retry.

## Scope and upgrade boundary

The public numeric-loopback bind fix, Windows process/terminal/pipe adaptations, launchers, installer framework, dependency pins and licenses are preserved. Windows and Linux native release acceptance is pending. Local implementation regression evidence covers 394 Python tests and 34 JavaScript scripts; this source freeze additionally checks its archive/manifest and preserved platform merge, without claiming final native certification.

A complete installation/source upgrade from preview.8 is required: its old stable bootstrap cannot accept the new module in an ordinary program-only ZIP. Existing data and official sign-in must be retained by the release process. This candidate includes no live data, credentials, logs, local coordination files or private home-directory adapters.

The public agy-console companion retains explicit dispatch. The matching nine-file external-ai-team preview.4 is bundled separately and requires explicit installation. It guides an active Codex task through the configured independent roundtable; default assist remains single-provider. The publisher does not modify the locally installed Skill or live preferences. No background collector, scheduler or idle-Codex wakeup is added, and cross-window automatic adoption is not certified.

## Service identity assets

Codex uses the unmodified OpenAI service icon from `https://developers.openai.com/favicon.png` (1841 bytes; SHA-256 `8d5575ee667ff715cd3e3074d5296edc68d5aadd89720d203e13131b68b22a04`). Muse uses its unmodified service icon from `https://muse.ai/images/pwa_icons/apple/180.png?v=3` (5915 bytes; SHA-256 `2b22ce2eda8dbb9c145dfbd59e39458c144dcd82c42cb4cb446ec62fefc2b62d`). They are embedded in app.js, add no runtime network requests, and identify those services; vendor marks remain their owners' property and imply no official affiliation. The root icon.svg is unchanged.
