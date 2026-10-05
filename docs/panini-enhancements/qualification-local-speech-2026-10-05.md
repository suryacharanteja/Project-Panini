# V2-27 offline dictation development checkpoint — 2026-10-05

## Delivered

- Pinned `faster-whisper==1.2.1`, `av==18.1.0` and a SHA-256-verified MIT model revision.
  The unsigned local installer bundles the model. Runtime inference uses a local path only.
- Private-mode dictation always selects the local engine, including when a cloud operation is
  injected into the app. Fallback mode tries local transcription after a cloud failure.
- The authenticated Electron controller can record up to 30 seconds and place the transcript
  in the composer for review. Live cloud voice is hidden when private mode is selected.
- Model readiness reports when the library, files or local fixed drive are unavailable.

## Verification

- Local speech and coordinator API tests cover private routing, controller authentication,
  an injected cloud-operation refusal, fallback and local model readiness.
- A Windows SAPI-generated WAV saying "Hello Panini. Open Notepad and type hello" was transcribed
  by the pinned local model without a model download in 1.17 seconds. The command phrase was
  recognized; the proper name "Panini" was misheard. This is one synthetic utterance, not a
  speech-accuracy benchmark.
- The frozen backend runs a local silent-WAV inference check during packaging. The full
  `npm test` gate passed: 1,286 backend tests, 16/16 scripted offline cases, 125 CLI
  tests with 1 skipped, frontend/browser/phone/search/Admin/shell suites and typechecks.
  The packaged-runtime and frozen-model smoke are release gates for this increment.

## Still open

The complete offline Notepad task, physical microphone capture, push-to-talk speech UX,
audible TTS playback, the comparative quality guard and whole-process blocking-proxy egress
test remain unqualified. V2-27 acceptance boxes stay unchecked. Red-team stays disabled and
deferred. This is an unsigned local development candidate, not a production release.
