---
name: mobile_ticket_check
description: Sanity-check the mobile/ app and reload the simulator after implementing a MOB-* ticket. Use whenever a mobile ticket's code changes are done and before marking it complete, or whenever the user asks to "check the mobile app", "reload the simulator", or "make sure nothing broke".
user-invocable: true
allowed-tools:
  - Read
  - Bash(cd *)
  - Bash(npx *)
  - Bash(npm *)
  - Bash(lsof *)
  - Bash(kill *)
  - Bash(ps *)
  - Bash(tail *)
  - Bash(grep *)
  - Bash(sleep *)
  - Bash(curl *)
  - Bash(cat *)
  - Bash(conda run *)
---

# /mobile_ticket_check — Post-ticket mobile sanity check

Run this after implementing (or before marking complete) any MOB-* ticket from
`docs/MOBILE-TICKETS.md`. It exists because dependency drift in `mobile/` has
broken the simulator silently in the past (see incident notes below) —
catching it here costs seconds; catching it during manual testing cost an
entire debugging session.

All commands run from `mobile/` (`cd /Users/nicolehui/carb-counter/mobile` first).

## 1. Dependency guardrails (fast, run first)

```
npx expo-doctor
```

- Must report **all checks passed**. If anything fails (version mismatches,
  duplicate native modules, invalid app.json fields), fix it before
  continuing — do not proceed with a red doctor output.
- Fix drifted versions with `npx expo install --fix` (or `npx expo install
  <pkg>` for a single package), never a bare `npm install <pkg>` for anything
  with native code. Re-run `expo-doctor` after fixing until clean.
- If a package needed by `babel.config.js` or any other root-level config
  (e.g. `babel-preset-expo`) isn't hoisted to top-level `node_modules`, add it
  explicitly to `package.json` rather than relying on hoisting — hoisting is
  not stable across installs.

## 2. Type check

```
npx tsc --noEmit
```

- New errors introduced by this ticket must be fixed.
- A short list of **pre-existing** errors unrelated to mobile tickets is
  currently tolerated (e.g. the `navigation/index.tsx` `fullScreenModal`
  stack-navigator typing issue) — don't let those block you, but don't add to
  them either. If unsure whether an error is pre-existing, check `git diff`
  before assuming it's fine.

## 3. Restart Metro with a clean cache and confirm it bundles

Stale Metro/babel-transform caches mask the very fixes you just made, so
always restart with `-c` after touching dependencies — a code-only change can
skip this step.

```
lsof -nP -iTCP:8081 -sTCP:LISTEN | awk 'NR>1{print $2}' | xargs -r kill
sleep 1
(nohup npx expo start -c --ios > /tmp/expo-start.log 2>&1 &)
sleep 14
tail -n 60 /tmp/expo-start.log
```

- Look for `iOS Bundled ... node_modules/expo/AppEntry.js (N modules)` with no
  `ERROR` lines above it. Deprecation `WARN` lines are fine.
- If you see `Cannot read properties of undefined (reading 'transformFile')`
  or a `MODULE_NOT_FOUND` from inside `@babel/core`, a babel preset/plugin
  needed by `babel.config.js` failed to resolve — check `node_modules/<pkg>`
  actually exists at the top level, add it to `package.json` if missing, then
  re-run `npm install` and retry this step.
- If port 8081 shows a session already running that belongs to the user (they
  started it in their own terminal), don't kill it out from under them without
  saying so — tell them what you found and that a reload/restart is needed.

## 4. Reload the app

If Metro bundled cleanly and a simulator session is open, reload:

```
xcrun simctl list devices booted
```

Confirm a device is booted, then tell the user to reload (shake gesture →
Reload, or press `r` in the Metro terminal) — or reload it yourself if you
have `claude-in-chrome`-equivalent simulator control available. Report what
you observed in the bundler log, not a guess about whether it will work.

## 5. Backend connectivity (only for tickets that call the API — MOB-003+)

Any ticket that exercises `submitMeal`/`pollMealStatus`/`logMeal`/etc. needs a
live backend. "Fetch failed" or "Could not connect to server" in the app
almost always means one of these two things, not a bug in the mobile code:

```
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/health --max-time 5
```

- **No output / connection refused** → nothing is listening on 8000. Start it
  from the repo root (not `mobile/`), in the `carb_counter` conda env:
  ```
  conda run -n carb_counter uvicorn api:app --port 8000
  ```
  Run this with the Bash tool's `run_in_background` (or a backgrounded shell
  the harness tracks) — a bare trailing `&` in a one-off Bash call can get
  reaped when that call's shell exits, silently leaving nothing running.
- **200 but the app still 401s** → `API_KEY` in the root `.env` (server) and
  `EXPO_PUBLIC_API_KEY` in `mobile/.env` (client) must match exactly. Neither
  file is committed; check both exist and agree before assuming the code is
  wrong. `mobile/.env` also needs `EXPO_PUBLIC_API_URL=http://localhost:8000`
  for local dev vs. the deployed `sikfan-api.fly.dev` Fly app — local is the
  default for active ticket work so edits to `api.py`/pipeline code don't need
  a deploy cycle; switch to Fly only to test the deployed environment
  specifically.
- Changing `mobile/.env` requires a Metro restart (step 3) to pick up the new
  `EXPO_PUBLIC_*` values — editing it alone does not affect an already-running
  bundler.

If everything above checks out and you still see a client-side crash on
upload (not a connection error), see the `FormDataPart` note below — that's
an Expo SDK compatibility issue, not a connectivity one.

## Why this exists (incident notes)

During MOB-005 (camera screen), adding `expo-camera`/`expo-image-picker`
surfaced two unrelated, silent breakages:

1. Several core Expo packages (`expo-file-system`, `expo-font`,
   `expo-modules-core`, `expo-status-bar`, `@expo/vector-icons`, ...) had
   drifted far from the versions SDK 56 expected, breaking native view
   manager registration (`viewManagersMetadata of null`).
2. After realigning versions with `expo install --fix`, `babel-preset-expo`
   stopped being hoisted to the top-level `node_modules`, so Metro's
   transformer failed to construct (`Cannot read properties of undefined
   (reading 'transformFile')`) — it had never been a direct dependency, only
   reachable by lucky hoisting.

Neither would have been caught by `tsc` or by reading the diff. `expo-doctor`
catches (1) directly; explicit dependencies (rather than relying on hoisting)
prevent (2).

During MOB-006 (analyzing screen), wiring `submitMeal` to a real image upload
surfaced a third, also-silent breakage plus a plain misconfiguration:

3. `formData.append('file', { uri, name, type })` — the classic React Native
   file-part shape used in the MOB-003 implementation notes — throws
   `Unsupported FormDataPart implementation` under SDK 56's `expo/fetch`
   ("winter") runtime. That runtime only serializes strings and `Blob`/`File`
   instances; the plain `{uri, name, type}` object isn't one. Fix: build the
   part from `expo-file-system`'s `File` class instead, which implements
   `Blob` —
   ```ts
   formData.append('file', new File(imageUri) as unknown as Blob, 'meal.jpg');
   ```
   `tsc` and `expo-doctor` both stay green through this; it only shows up at
   runtime on an actual upload, so exercise the real upload path (not just a
   bundle check) before marking an upload-touching ticket complete.
4. Separately, "Fetch failed" / "Could not connect to server" was just the
   FastAPI backend not running locally — see step 5 above.
