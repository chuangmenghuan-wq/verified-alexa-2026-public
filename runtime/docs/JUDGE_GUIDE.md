# Judge guide — R14

Start with README.md. Use the dedicated owned browser window. Setup needs Python3.12x64 and installed Chrome/Edge; dependencies are offline. Evidence distinguishes a real local implementation from a simulated Alexa+ surface.

## Two-minute interaction

Choose Tidal Clock (60seconds), start at a position before45seconds, end at45, one automatic repair. The preview states the concrete promise. Confirm. The UI can verify current playback while the overall promise is still running.

Break the media route: it really returns503. A command response alone does not establish success. The unused repair returns the same content to the latest independently observed position. Break again: the durable allowance is exhausted. Review a repair request, then issue Stop/Cancel; the earlier confirmation must be rejected and the player must stay stopped. New viewing authorization is concrete, never inferred from an old Yes.

For restart proof, close with Ctrl+C and launch again with the same --state-dir. Recheck/repair preserve mission/history and resolve the actual current owned runtime. Revoked work cannot auto-resume. Local import accepts the formats/bounds stated by the UI; unknown physical-TV/sports requests are rejected.

## Completion

For a segment, VERIFIED_DONE requires a fresh observation of the requested content stopped at the requested media endpoint. PLAYING_VERIFIED is only a stage. At the endpoint the screen shows the actual position and stopped state. Evidence expires to awaiting confirmation.

Technical details are expandable. MCP transcripts include negotiation, tools and fresh observation binding. The screen and response must agree; attached model reviews do not override behavior.

## Reproduction

Run scripts/judge_smoke_current.py from a new extraction. Red and green evidence includes opacity, occludingB, frozen-screen, selector-replacement, pause/stop-decoy, stale/canceled consent, three race/restart timings, budget persistence and segment boundaries. See the delivery manifest for the exact final source/package/video hashes and actual outcomes. Controlled comparison is against a declared command-only baseline, not commercial Alexa.

The public video/GitHub access are not submitted by this local build. Human microphone/usability and non-Windows execution remain UNVERIFIED.
