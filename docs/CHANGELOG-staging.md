# Staging changelog — local commits awaiting batched push
Rule: every completed workstream = one local commit + one line here.
Push batches happen only on explicit go-ahead. `main` stays always-runnable
(launchd runs this working tree).

## 2026-07-05
- (run start) P1 "Earn trust" begins — quality-gated, in order. Downloads
  pre-approved; user-action items collect in docs/NEEDS-YOU.md.

## Local commits (unpushed)
- `de5ab7f` docs: research corpus + dev plan
- `d06ae7a` docs: staging changelog, needs-you, brief spec
- `<foundation>` server: aux-module route registry + static aux JS loader (P1 foundation)
  → register_get/register_post + RouteCtx; aux_*.py auto-exec'd; /aux_*.js static.
    Every P1 feature now drops in as a self-contained aux module — no dispatch surgery.
- `<p1.1>` P1.1 Editable Memory — aux_memory.py (734L) + aux_memory.js (679L) + 2 index.html hooks.
  View/add/edit/delete agent memories from Mind view. §-delimiter-aware core-file editor
  (byte-identical to memory_tool, never trips drift detector), freeform topic files, soft-delete
  to trash + restore, snapshots + JSONL recorder (P1.2 contract), etag concurrency (409), flock
  (423), char-limit/path/symlink/casefold guards, core-file delete refusal (403).
  VERIFIED: full curl matrix, headless render, and LIVE `hermes -z` codeword drill —
  agent read a dashboard edit (BANANA→KUMQUAT→BANANA), zero drift, 600 perms preserved.
- `<p1.2>` P1.2 Flight Recorder + Undo — aux_recorder.py (1048L) + aux_recorder.js (459L)
  + hermes_rpc.py hook (RECORDER_HOOK, 2 touches) + 1 index.html script tag.
  Rides hermes-agent's OWN git checkpoint store (enabled checkpoints.enabled=true, store live).
  Three legs: ws tool.start/complete (live), 5s state.db reconciler (ALL surfaces), upstream
  pre-write snapshots (race-free). recorder.db (WAL,0600,tool_call_id-dedupe). Console "Flight
  Recorder" lane with per-kind reversibility chips + Undo. /api/recorder + /api/undo (whitelist
  refusal for irreversible, sha256 conflict+force, single-file restore / created-file→undo-trash,
  undo-is-itself-undoable). VERIFIED: 176 actions reconciled from state.db, byte-identical undo
  round-trip via direct-checkpoint harness, all refusal paths, headless render. ws-live leg +
  approved-write drill deferred to P1.4 (needs UI approval; -z can't approve under manual mode).
  NOTE: enabling checkpoints.enabled means the agent now git-snapshots before every file write.
- `<p1.3+p1.4>` P1.3 Graduated Permission Tiers + P1.4 approval-loop drill.
  permissions.py (740L engine) + aux_permissions.py (49L routes) + aux_trust.js (398L Trust panel)
  + hermes_rpc.py enforcement branch (decide→respond, sends only once/deny) + 1 index.html tag.
  17 action-classes over 73 dangerous-pattern keys; AUTO/ASK/NEVER tiers with safety FLOORS
  (critical classes can never be auto), tamper-detection sidecar (auto→ask on out-of-band edit),
  audit JSONL. SHIPPED DEFAULT = all-ask (installing changes nothing; user graduates trust in panel).
  Also fixed switch_model: kickstart -k → bootout/bootstrap (the real "switch button" bug;
  KeepAlive service wasn't reloading the new model).
  LIVE-DRILLED all 3 tiers through real Qwen3-30B turns: ASK→card fired→deny→blocked;
  NEVER→auto-denied; AUTO→auto-approved+command ran; command_allowlist unchanged (interop held);
  P1.2 ws-leg captured 3 origin=ws terminal rows. Audit log shows asked/user-deny/auto-denied/auto-approved.
  KEY FINDING: Hermes-3-8B does NOT reliably call tools (deflects); Qwen3-30B-A3B does. See docs/FINDINGS.md.
