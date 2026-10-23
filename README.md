# Verified Alexa — A Promise You Can Take Back

This is the standalone source repository for the submitted Amazon Build, Ship, Shape 2026 Alexa+ track project. It runs a **simulated Alexa+ interface** with real local media playback, a Streamable HTTP MCP server, and a separate observer that checks the visible result before a viewing promise can complete. It is not native Alexa integration or a production service.

## Watch the submitted demo

[Sarah narrated demo on YouTube](https://youtu.be/cOaBa-BLqcM). The spoken narration and captions explain a real R14 recording. The demo shows a command claiming success while the observed route fails, one permitted recovery, a second interruption after the repair allowance is exhausted, Stop revocation, rejection of an old confirmation, and a fresh verification of the requested end position. The route interruption is a disclosed fault injector.

## Install and run on Windows

Prerequisites: Windows x64, Python 3.12 x64, and Chrome or Edge. The bundled wheelhouse supports an offline Python dependency install. No cloud account, model API key, Future Ability installation, or login is required.

```powershell
cd runtime
powershell -ExecutionPolicy Bypass -File ./setup_windows.ps1 -Python python
./.venv/Scripts/verified-alexa-sim.exe --port 0 --state-dir ./user-state
```

The launcher prints local UI and MCP URLs and opens the dedicated observed player. The MCP server uses Streamable HTTP protocol version 2025-11-25 at the printed `/mcp` URL. See [runtime/README.md](runtime/README.md) and [the judge guide](runtime/docs/JUDGE_GUIDE.md) for the form, text commands, synthetic audio fixtures, and local media controls. Use **Play Clip A** for a minimal request. In the agreement UI, choose the 60-second Tidal Clock clip, set an end at media timestamp 45, choose one allowed repair, then review and confirm. Stop or Cancel revokes the previous authorization; a later continuation needs a new confirmation.

## Reproduce the checks

Use a fresh clone or source ZIP. From `runtime` after installation:

```powershell
./.venv/Scripts/python.exe scripts/judge_smoke_current.py
./.venv/Scripts/python.exe scripts/fresh_entry_r14.py
```

The first command exercises the local browser, MCP route, independent observer, Stop, recovery, and regressions. It writes a JUnit result under `runtime/evidence/`. The second exercises the installed console entry, verifies the public source file manifest, rejects a false success after an independent HTTP 503, checks repair across a restart, checks that playing is not segment completion, confirms the actual end position, and checks cancellation/revocation. It writes `runtime/validation/FRESH_ENTRY_RESULTS.json`. Both commands should be run with a fresh `runtime/user-state` directory; the tests create their own temporary state. Historical R14 acceptance was 113 tests and 7 installed-entry checks; these are reproducible claims, not a substitute for each judge's fresh run.

## Source and limits

`runtime/src/verified_alexa` contains the simulator, MCP client/server, agreement and recovery logic, observer and browser/media code. `runtime/tests` contains the executable tests. `runtime/scripts` contains the smoke and media scripts; `runtime/wheelhouse` contains pinned Windows dependency wheels; `runtime/src/verified_alexa/assets` contains the local demonstration media and UI resources. `runtime/requirements-windows.lock` and `runtime/pyproject.toml` state dependencies and versions. `runtime/FILE_MANIFEST.json` records SHA-256 hashes for retained R14 code, tests, assets, wheels, and unchanged guides. Edited publication guides are outside that manifest.

The application code, tests, and media assets come from the frozen R14 / 0.4.0 build. This public distribution removes old machine-specific raw traces, historical submission receipts, and local paths; its manifest covers the unchanged retained R14 files. The frozen R14 archive itself was not edited. Fresh runs regenerate local evidence. The original full R14 archive SHA-256 was `d92d6d00b3d57c2b655600154e2e093555e9e379e0e1178905857adb4ae78be7`.

Real locally: owned media and HTTP routes, actual Chrome/Edge player observations, one bounded repair, persistent mission and revocation state, and MCP requests. Simulated: Alexa+ identity/household surface and prerecorded or synthesized voice fixtures. Unverified: native Alexa hardware/service, physical TV, human microphone/acoustics/usability, Linux/macOS, and production security. An unavailable or undecodable picture remains `NOT_VERIFIED`; a command response alone is never completion proof. The observer depends on the local browser, Chrome DevTools protocol, and OS being trustworthy. The agreement monitor is bounded to 120 seconds while the backend runs.

## Rights

The project-owned source code and documentation are licensed under [MIT](LICENSE). Original procedural demo media are CC0 1.0 as described in [MEDIA_LICENSE.md](runtime/docs/MEDIA_LICENSE.md) and the asset-specific license. Bundled dependency wheels retain their upstream licenses and notices; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). No Future Ability production code or credentials are required by this repository.
