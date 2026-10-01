# Search

## Implemented

- Web retrieval through OpenAI Responses web search, with Jev intent/relevance judgments through AI Gateway.
- Streaming result lanes, deduplication, ranking, linked sources, failure reporting and cancellation.
- Search UI source/time controls, bounded cache and offline shell. Unknown publication dates remain unknown.
- PANINI returns up to ten ranked HTTP(S) results. Its adapter accepts a query up to 300 characters and records source evidence.

## Prerequisites and ownership

Configure `OPENAI_API_KEY` and `AI_GATEWAY_API_KEY`; build the worker with `npm run panini:build`. Select **Search** without a target. Readiness checks credentials are present and the worker exists; a real query validates provider access.

Source: `panini-core/capabilities/search/`, `backend/capabilities/search.py`. The adapter owns the worker; the coordinator owns the task and Stop.

## Limits and validation

Partial source failures remain partial. Rankings and AI summaries are judgments, not guarantees of factual correctness. Retrieval depends on provider access and network availability. Search does not click websites or run local commands.

Offline: `npm run test:search`. Integration: `npm run panini:smoke -- --search`. Live integration consumes provider credits. Manual check: ask for official Windows UI Automation documentation and open a returned source.

