# PANINI third-party notices

The PANINI installer includes open-source dependencies. Their license texts and
notices are collected into `runtime/licenses/python`, `runtime/licenses/npm`,
and `runtime/licenses/upstream` during packaging. Dependency inventories are
included in the same directory. Consult those files for the terms applicable
to each bundled component.

PANINI's own source is distributed under the repository's `LICENSE`.

## Harvested sources (PANINI v2)

PANINI v2 ports patterns and, where noted, code from these projects. Ported files carry a header naming
their source. Details per file: `docs/panini-enhancements/harvest-map.md`.

| Source | Licence | Use |
| --- | --- | --- |
| CopilotKit OpenDots (snapshot 2026-10-01) | MIT, Copyright (c) Atai Barkai | Patterns and ported code (computer service, review cards, voice receipts, Slack allow-list, URL guard, Spaces editor) |
| CopilotKit OpenBot (revision `b6932d31a8d6e7896c15139dfc27a6c6911deb27`) | MIT | Sandbox computer image and supervisor, built from the pinned revision |
| Microsoft Playwright 1.63.0 | Apache-2.0 | Bundled MCP server used unmodified; CDP relay protocol ported to Python |
| AG-UI protocol | MIT | Event models and encoder |
| SYSTRAN faster-whisper base model (`ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66`) | MIT, per the [model card](https://huggingface.co/Systran/faster-whisper-base) | Pinned local speech recognition weights and tokenizer; verified at packaging by file SHA-256. The model card is included beside the weights. |
