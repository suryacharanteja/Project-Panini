# Project Panini — Change to Implementation Approach

**Document Type:** Implementation — Process  
**Phase:** 06 — Implementation  
**Version:** 1.0  
**Status:** ADOPTED  
**Date:** 2026-09-09  

---

## 1. What changed

The LLD (`05_LLD/00_LLD_Index.md` §5) sequenced implementation into eight stages, each completed before the next began. That ordering is still correct — it is dependency-driven, and building the security kernel first was the right call.

**What changes is the size of a shippable increment and when the user tests it.**

| | Original | Adopted |
|---|---|---|
| Unit of delivery | A stage (e.g. "voice pipeline") | A sub-increment the user can exercise (e.g. "it speaks") |
| User testing | After a stage completes | After every increment |
| Feedback loop | Stage-length | Increment-length |

Stages are unchanged. They are now delivered in slices.

---

## 2. Why

Five defects reached working code during Stages 1–3. **Every one passed the test suite and was found only by running the system.**

| Defect | Consequence had it shipped | Test suite verdict |
|---|---|---|
| Orchestrator sent tool *names* without parameter schemas | The model guesses argument names; tools fail | ✅ passing |
| Tool descriptions omitted the accessible directories | User is prompted to approve actions that cannot succeed | ✅ passing |
| Relative paths resolved against the process CWD | Files written to an unpredictable location | ✅ passing |
| Fact retrieval missed inflected forms (`authorised` / `authorises`) | A relevant fact is never recalled — **and for a fact of untrusted origin, DD-121 M-2 never fires** | ✅ passing |
| First TTS chunk capped at 60 rather than 40 characters | Time-to-first-audio misses its budget | ✅ passing |

The common cause: **test doubles encode the author's assumptions.** A fake registry that returns a `ToolSpec` cannot reveal that the real path needed a full schema. A retrieval test using exact word matches cannot reveal that real phrasing shares no tokens.

The fourth defect is the clearest argument. A relevance bug became a security bug: a fact that is never retrieved cannot trigger the taint it carries. Nothing in the suite could have caught it, because the suite tested the mechanism and not the path into it.

Deferring user testing until Stages 4–8 were complete would have accumulated defects of this kind for weeks, then surfaced them together — at the point where they are most expensive to attribute and fix.

---

## 3. What is not changing

| Unchanged | Reason |
|---|---|
| Stage order | Dependency-driven; the security kernel must precede tools |
| The Decision Register as source of truth | Traceability is unaffected by delivery cadence |
| Negative security tests as a blocking gate | Still the highest-value suite |
| LLD as the implementation blueprint | Increments implement it in slices, not instead of it |
| Documentation of new decisions | Anything learned in an increment is registered before it is built on |

**This is a change to cadence, not to rigour.**

---

## 4. Revised increment plan

| # | Increment | User-visible outcome | Stage |
|---|---|---|---|
| ✅ | Security kernel · tools · providers · memory | Text conversation with tools and audit | 1–3 |
| **4a** | **TTS** | **The assistant speaks its replies** | 4 |
| 4b | STT + push-to-talk | Speak to it via a keypress | 4 |
| 4c | Wake word + VAD | Hands-free | 4 |
| 7a | Web search | One connector, no OAuth | 7 |
| 7b | Gmail | The threat model exercised for real | 7 |
| 5a | Minimal Electron window | A window instead of a terminal | 5 |
| 6a | VRM avatar | Visible, lip-synced presence | 6 |
| 8a | Plugin sandbox | Third-party extension | 8 |

Each increment ends with a user-testable capability and a short note of what to try.

---

## 5. Definition of done, per increment

1. Implemented against its LLD section
2. Security tests green (blocking)
3. **Exercised end-to-end by the user**
4. Defects found in (3) fixed before the next increment starts
5. Any new decision recorded in the Decision Register

Step 4 is the one that changes the outcome: fixing five real annoyances is worth more than adding a feature on top of them.

---

## 6. Risk this introduces

| Risk | Mitigation |
|---|---|
| Feedback loops slow overall delivery | Accepted — rework discovered late costs more than it saves |
| Increments encourage local fixes over design changes | A defect that contradicts a decision goes to the Decision Register, not into a patch |
| User testing is unstructured and misses areas | Each increment ships with a short, specific test guide (`02_Testing_Guide.md`) |

---

## 7. Decision

| Proposed ID | Decision |
|---|---|
| **DD-157** | **Implementation is delivered in user-testable increments rather than complete stages.** Five defects in Stages 1–3 passed the test suite and were found only by running the system; one turned a relevance bug into a security bug. Test doubles encode the author's assumptions, so user testing is treated as a required step in the definition of done, not a phase that follows implementation. |
