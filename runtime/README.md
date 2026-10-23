# Verified Alexa — a viewing promise you can change (R14 / 0.4.0)

Continue a local video from its freshly observed position, stop at a specific media timestamp, and allow at most one automatic repair. A separate observer checks the actual visible picture. Stop and Cancel revoke earlier actions. A playing stage is not completion of the whole segment promise.

## Windows quick start

Prerequisites: Windows x64, Python3.12 x64 and installed Chrome/Edge. The included wheelhouse supports Windows CPython3.12. No model key, cloud account or Future Ability installation is required.

From the extracted folder in PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File ./setup_windows.ps1 -Python python
./.venv/Scripts/verified-alexa-sim.exe --port 0 --state-dir ./user-state
```

The command prints the local URLs and opens the dedicated observed window. Ctrl+C stops only owned services. A different tab is a remote control view. Browser discovery can use VERIFIED_ALEXA_BROWSER.

For deep Windows extraction paths, the app creates a short dedicated browser profile under the system temporary directory. Durable mission, bookmark, repair allowance and revocation remain in your chosen state directory. A personal browser profile is never used.

1. Choose the original60-second Tidal Clock clip or import a supported local WebM/MP4 file.
2. Review the concrete start/end and repair allowance, then confirm the viewing promise. End45 means media timestamp00:45, not45seconds from now.
3. The observation card shows current content/position and actual compositor thumbnail. Evidence expires to awaiting confirmation; the segment remains active while playing.
4. Break the route using the disclosed fault control. The command may be accepted while the media really returns503. The observer refuses completion; an authorized unused repair restores the same route/content/position.
5. After the allowance is used, another fault asks for a concrete new authorization. Stop/Cancel reject old confirmations and delayed effects.
6. A full restart with the same user-state preserves the logical target, history, bookmark, allowance and revocation. Recheck reads the actual replacement runtime. Confirm before resuming revoked work.

```powershell
./.venv/Scripts/python.exe scripts/judge_smoke_current.py
```

See [Judge Guide](docs/JUDGE_GUIDE.md), [architecture](ARCHITECTURE.md), [media license](docs/MEDIA_LICENSE.md), and [evidence index](docs/EVIDENCE_INDEX.md). Fresh final test counts and hash bindings live in the delivery manifest, not in a manually asserted PASS label.

## Scope

**Real locally:** owned media HTTP, registered source hashes/independent decoder, pinned native video, actual Chrome compositor/time window, real route failures and repairs, durable mission/revision/attempt history, Streamable HTTP MCP2025-11-25, goal-bound previews and revocation.

Consumer playback controls and confirmations use MCP. An empty or undecodable native player may be paused while its visible outcome remains NOT_VERIFIED; a Stop never loads or plays new content merely to obtain a PASS.

**Simulated:** Alexa+ consumer identity and household setting. Original procedural scenes, synthesized narration and prerecorded speech fixtures are disclosed. Optional browser TTS/recognition has a text fallback.

**UNVERIFIED:** physical microphone, acoustic quality, human usability (no participants), native Alexa+, physical TV, Linux/macOS, production security and hostile OS/Chrome/CDP attestation. Supported local files are bounded formats/content, not arbitrary media understanding. Imported sources remain local. A hash receipt is historical byte self-consistency, not present truth.

The bounded monitor works while the owned backend runs. Expired monitoring needs fresh authorization. A live observation establishes a short current-state window, not indefinite playback. The public repository and submitted demo are linked from the top-level README.
