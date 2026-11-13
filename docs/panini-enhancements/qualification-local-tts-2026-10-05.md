# V2-27 Windows local speech checkpoint — 2026-10-05

The Windows shell has an owner-initiated **Read reply locally** control in private and fallback modes.
It calls a lease-authenticated coordinator endpoint with at most 500 characters. The backend runs an
installed Microsoft SAPI voice in a child process with a 20-second timeout; text goes through stdin,
not command arguments. SAPI receives the non-XML flag. The result must be a bounded mono 16-bit WAV
before the shell creates a temporary audio blob URL. Stop cancels pending playback in the UI and
releases the URL. No voice package or model is redistributed.

Source verification used a real installed Microsoft Windows voice and validated the WAV header,
sample format and duration. API tests cover missing credentials, wrong lease, disabled mode and input
bounds. The frozen backend smoke runs synthesis from the packaged executable; its result and the
shell playback path must be recorded with the development release. Microsoft documents the
[`SVSFIsNotXML` flag](https://learn.microsoft.com/en-us/previous-versions/windows/desktop/ee125223%28v%3Dvs.85%29)
used to keep reply text literal.

This proves local WAV synthesis on this PC. It does not prove audible output from the physical
speaker, voice availability on another Windows installation, zero egress under a blocking proxy,
live microphone quality, a qualified local planner, or the complete offline Notepad task. V2-27
and the full LLD program remain in progress. Red-team is disabled and deferred.
