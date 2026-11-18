# Local preview.13 developer candidate

This source candidate combines the public preview.8 distribution baseline with reviewed preview.9–.13 application changes. It is a local handoff, not a published Release. Existing platform launchers, Windows Job/installer, licenses, pinned build dependencies and explicit companion installation are retained.

Codex-dispatched requests have a distinct sender identity. Actual observed Muse/Claude contributions and Codex summaries can be recorded in the same chat without adding CLI turns or token totals. Chat controls use less vertical space. New `multi_ai_collaboration.py` provides exact-chat provider permissions, configured foreground startup metadata, frozen moderator roles and observed transcript validation.

## Build and verification

Use `python3 scripts/audit.py`, `python3 scripts/check.py --report /outside/source/checks.json`, and `python3 scripts/test_framework.py`. The existing Windows-native workflow builds and tests the real installation lifecycle; those jobs must pass at the final publication commit. Local source tests do not prove Windows or Linux hardware acceptance.

The local tested panel version is preview.13. This candidate preserves the public numeric-loopback startup fix and merges existing Windows adaptations rather than replacing them with POSIX process code. Per-file SHA256, modes and provenance are provided beside this candidate in the developer handoff.

## Full upgrade required

Preview.8's bootstrap excludes the new module. Upgrade to this version through a complete installation/source package while retaining the existing data directory and official sign-in. Do not advertise a program-only ZIP to preview.8 clients. `scripts/package_release.py` omits program updates by default; its explicit program-update option is only for installations already using the complete new bootstrap. No data or official credentials are bundled.

## Companion and external Skill scope

The existing agy-console companion and explicit installers are preserved from the public baseline. They can still dispatch through the fixed binding, but do not automatically orchestrate a full multi-website roundtable. The separately maintained external-ai-team candidate covers provider switches, one-entry routing and controlled foreground startup. Its owner must confirm compatible pairing before publication; the installed private local adapter is never part of this source.

Roundtable contributions currently require an actual completed Agy anchor. The active Codex observes real external replies and records them with `bridge.py --record-message observed.json`; each receipt carries exact thread, anchor, provider, stage, source URL and stable source ID. The backend starts no model or browser for receipt recording. Automatic cross-website two-round dispatch is not integrated into the shipped Skill. No background collector or idle model wake-up is provided.

The historical publication-verification.json describes preview.8, not this unpublished candidate. Current exact-commit CI, native installers and public attachment verification remain the publisher's release gates.


## Publisher pairing

The complete release includes the exact public external-ai-team preview.3 eight-file Skill for POSIX and Windows, with separate explicit installation instructions. The Skill owner confirmed canonical schema1/bridge/startup compatibility. Automatic cross-web roundtables remain absent. Mac Bark terminal notifications require the matching Build51 receiver; the producer and its synthetic tests are retained in this frozen source. Native final-commit CI and asset verification remain release gates.
