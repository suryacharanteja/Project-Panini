# Project Panini — Voice Pipeline LLD

**Document Type:** Low Level Design  
**Phase:** 05 — LLD  
**Module:** `panini/voice/`  
**Version:** 1.0  
**Status:** DRAFT — awaiting review  
**Date:** 2026-09-09  

---

## Resolves

| Open item | Source | Section |
|---|---|---|
| Sentence segmentation ("Dr.", "e.g.") | SR-06 | §5 |
| TTS chunk sizing rule | DD-106 | §5.3 — **re-derived from measurement** |
| Vocabulary biasing for "Panini" | DO-007, FR-113 | §3.4 |
| Interruption mechanics | FR-120–122 | §7 |

---

## 1. Module Layout

```
panini/voice/
├── pipeline.py          Voice state machine (HLD §5.1)
├── devices.py           Audio device singleton
├── asr/
│   ├── base.py          ASREngine ABC
│   ├── faster_whisper.py
│   └── factory.py
├── tts/
│   ├── base.py          TTSEngine ABC
│   ├── kokoro.py
│   └── factory.py
├── vad/
│   ├── base.py          VADEngine ABC
│   └── silero.py
├── wakeword/
│   ├── base.py          WakeWordEngine ABC
│   └── openwakeword.py
├── segmenter.py         ⚠️ Streaming sentence/clause chunking
└── playback.py          Audio output queue + interruption
```

---

## 2. Audio Configuration

| Parameter | Value | Rationale |
|---|---|---|
| Capture sample rate | 16,000 Hz | Whisper, Silero, OpenWakeWord all expect 16 kHz |
| Playback sample rate | 24,000 Hz | Kokoro output rate (measured) |
| Frame size | 512 samples (32 ms) | Silero VAD frame requirement |
| Channels | 1 (mono) | — |
| Format | float32 internally, int16 on the wire | — |

`devices.py` is a singleton resolving the default input/output device, negotiating sample rate, and falling back to the OS default on failure. It is shared by wake word, VAD, and capture — three consumers of one stream, so the device is opened once and frames are fanned out.

---

## 3. Speech-to-Text (`asr/`)

### 3.1 Interface

```python
class ASREngine(ABC):
    @abstractmethod
    async def transcribe(self, audio: np.ndarray, hints: TranscriptionHints) -> Transcript: ...

@dataclass(frozen=True)
class TranscriptionHints:
    initial_prompt: str | None = None    # decoder biasing — FR-113
    language: str = "en"

@dataclass(frozen=True)
class Transcript:
    text: str
    confidence: float | None
    duration_s: float
```

### 3.2 Configuration (DD-020, measured)

```python
WhisperModel(
    "base",                # DD-020 — `small` FAILED at 2.31s vs 1.5s target
    device="cpu",          # DD-097
    compute_type="int8",
    cpu_threads=0,         # all 20 threads
)
# transcribe(beam_size=1)  — greedy; beam search adds latency for no gain here
```

**Measured:** 0.75 s for a 3 s utterance (target ≤1.5 s).

### 3.3 Threading (HR-02)

Transcription is CPU-bound and takes ~0.75 s. Running it on the event loop would stall audio playback and SSE delivery.

```python
transcript = await asyncio.to_thread(self._model_transcribe, audio, hints)
```

### 3.4 Vocabulary biasing — the "Panini" problem (DO-007)

Validation measured **0/12 correct transcriptions** of the assistant's name, deterministically rendered as "Ponyny", "Ponyney", or "Pawnee".

**Two layers, applied in order:**

**Layer 1 — decoder biasing (FR-113).** faster-whisper accepts `initial_prompt`, which primes the decoder's vocabulary:

```python
DEFAULT_PROMPT = "Panini is an AI assistant."   # + user-configured vocabulary
```

Cost: a small number of prompt tokens per call. Negligible latency.

**Layer 2 — correction map (FR-116).** Applied to output as a whole-word, case-insensitive substitution:

```yaml
voice:
  stt:
    corrections:
      Ponyny:   Panini
      Ponyney:  Panini
      Pawnee:   Panini
      Ponini:   Panini
      Pawnini:  Panini
```

Seeded from measured mis-transcriptions. Word-boundary anchored so "Pawnee, Oklahoma" in a genuine context is not corrupted — an acknowledged trade-off recorded as an open item.

> **Layer 1 is expected to do most of the work; Layer 2 is the safety net.** Effectiveness must be re-measured with a **human voice recording** — the SAPI-generated test audio may itself mispronounce the name (Validation Test 7, outstanding).

---

## 4. Wake Word & VAD

### 4.1 Wake word (`wakeword/openwakeword.py`)

| Aspect | Design |
|---|---|
| Engine | OpenWakeWord (Apache 2.0, DD-025) |
| Model | Custom "Hey Panini" model; pre-trained fallback until trained |
| Threshold | Configurable, default 0.5 |
| Thread | Dedicated — continuous blocking read (HLD §7) |
| Suppression | **Disabled while `SPEAKING`** (FR-103) |

**Self-trigger prevention:** the pipeline sets `wake.suppressed = True` on entering `SPEAKING` and clears it on exit. Without this, the assistant's own voice through the speakers re-triggers it.

### 4.2 VAD (`vad/silero.py`)

| Aspect | Design |
|---|---|
| Engine | Silero VAD (DD-024) |
| Frame | 512 samples @ 16 kHz |
| Speech-start | 3 consecutive speech frames (~96 ms) — debounce |
| Speech-end | 25 consecutive silence frames (~800 ms) |
| Max utterance | 30 s hard cap |

**End-point delay is deliberate and is charged against the latency budget.** 800 ms of trailing silence is the cost of not truncating natural pauses. Validation Test 9 must measure whether it can be reduced.

VAD stays active during `SPEAKING` to detect interruption (FR-120) even though wake-word detection is suppressed.

---

## 5. TTS Segmentation (`segmenter.py`) — resolves SR-06

### 5.1 Why a library alone is insufficient

Off-the-shelf segmenters (`pysbd`, NLTK Punkt, spaCy) assume **complete text**. We segment a **token stream** — the LLM is still generating. The segmenter must decide boundaries incrementally, from partial input, without waiting for the response to finish.

**Design: a streaming state machine that uses an abbreviation table for boundary disambiguation.**

### 5.2 Boundary detection

```python
ABBREVIATIONS = {
    # titles
    "mr","mrs","ms","dr","prof","sr","jr","st","rev","hon",
    # latin
    "e.g","i.e","etc","vs","cf","al","viz",
    # business
    "inc","ltd","co","corp","dept","est","approx",
    # units / time
    "no","fig","vol","pp","ed","jan","feb","mar","apr","jun",
    "jul","aug","sep","oct","nov","dec","mon","tue","wed","thu","fri","sat","sun",
}

def is_boundary(buf: str, i: int) -> bool:
    """True if buf[i] terminates a sentence."""
    ch = buf[i]
    if ch not in ".!?…":
        return False

    # '!' and '?' are unambiguous
    if ch in "!?…":
        return True

    # 3.5  /  192.168.1.1  — digit on both sides
    if i > 0 and buf[i-1].isdigit() and i+1 < len(buf) and buf[i+1].isdigit():
        return False

    # preceding token is a known abbreviation
    word = _preceding_word(buf, i).lower().rstrip(".")
    if word in ABBREVIATIONS:
        return False

    # single initial:  J. R. R. Tolkien
    if len(word) == 1 and word.isalpha():
        return False

    # U.S.A. — internal dots
    if "." in _preceding_word(buf, i):
        return False

    # require whitespace after; if not yet available, DEFER (need more tokens)
    if i + 1 >= len(buf):
        return False                      # incomplete — wait
    if not buf[i+1].isspace():
        return False

    return True
```

**Deferral is the key streaming behaviour.** When a terminator arrives at the buffer's end, the segmenter cannot yet decide — it waits for the next token. `flush()` at stream end resolves any pending deferral.

### 5.3 🔵 Chunk sizing — re-derived from measurement

DD-106 specified "~65 chars" based on the TTFA target alone. Re-analysing the measured Kokoro data revealed a **sharp cost knee** that makes the rule stronger and its rationale different.

**Measured synthesis cost per additional character:**

| Range | s/char | Regime |
|---|---|---|
| 15 → 62 chars | **0.0083** | Linear, cheap |
| 62 → 121 chars | **0.0446** | **5.4× steeper** |
| 121 → 245 chars | 0.0444 | Same steep regime |

Audio duration is linear throughout (~0.055 s/char). **Synthesis is not.** Above roughly 62 characters, per-character cost jumps more than fivefold.

**Model below the knee:**

```
synth_seconds(c) ≈ 0.20 + 0.0083·c      for c ≲ 62
audio_seconds(c) ≈ 0.055·c
```

Fit check: c=15 → 0.32 s (measured 0.30) · c=62 → 0.71 s (measured 0.69).

**This explains the starvation observed in validation:**

```
"medium" sample, split by sentence only:
  S1 = 24 chars → synth 0.40 s, audio 1.32 s
  S2 = 95 chars → ABOVE THE KNEE → synth 3.18 s

  S1 playback: 0.38 s → 1.70 s
  S2 ready at: 3.56 s
  ⚠️ 1.86 s gap        (matches the ~1.7 s measured)

Same text, capped at 55 chars:
  S1  = 24 chars → synth 0.40 s, audio 1.32 s
  S2a = 48 chars → synth 0.60 s, audio 2.64 s
  S2b = 47 chars → synth 0.59 s, audio 2.59 s

  S2a ready at 0.98 s, needed at 1.70 s  ✓
  S2b ready at 1.57 s, needed at 4.34 s  ✓
  No gap.
```

**Rules:**

| Rule | Value | Basis |
|---|---|---|
| **First chunk** | ≤ **40 chars** | `0.20 + 0.0083·40 = 0.53 s` TTFA, inside the 0.7 s budget with margin |
| **Subsequent chunks** | ≤ **55 chars** | Stays below the 62-char knee with headroom |
| **Hard maximum** | 60 chars | Never enter the steep regime |
| **Minimum** | 15 chars | Below this, fixed overhead dominates |

> **The cap applies to *every* chunk, not just the first.** DD-106 framed the cap as a TTFA optimisation; measurement shows it is equally a *throughput* optimisation. Exceeding it costs 5.4× per character.

### 5.4 Clause splitting

A sentence longer than the cap is split at clause boundaries, in priority order:

| Priority | Split point |
|---|---|
| 1 | `;` `:` `—` |
| 2 | `,` followed by a coordinating conjunction (`and`, `but`, `or`, `so`, `yet`) |
| 3 | Any `,` |
| 4 | Whitespace nearest the cap (last resort) |

Splitting never occurs inside a quoted string, parenthetical, or code span.

### 5.5 Streaming interface

```python
class StreamingSegmenter:
    def feed(self, token: str) -> list[Chunk]:
        """Append token; return any chunks that are now complete."""

    def flush(self) -> list[Chunk]:
        """End of stream — emit whatever remains, resolving deferrals."""
```

**Chunk index 0 uses the 40-char cap; all others use 55.**

---

## 6. TTS Synthesis (`tts/kokoro.py`)

### 6.1 Warm-up (DD-105)

Measured: a cold first call took **1.76 s** to synthesise 1.07 s of audio — slower than realtime. The same call warm took **0.30 s**.

```python
async def start(self) -> None:
    self._kokoro = Kokoro(model_path, voices_path)
    await asyncio.to_thread(self._kokoro.create, "Ready.", voice=self._voice)
    self._warm = True          # FR-720 — synthesised output is discarded
```

### 6.2 Queue and buffering (DD-106)

```
Segmenter ──► chunk queue ──► worker thread ──► audio queue ──► playback
                                                  (depth ≥ 1)
```

The worker synthesises chunk *N+1* while chunk *N* plays. With the §5.3 caps, synthesis is ~4.7× faster than playback, so a buffer depth of 1 is sufficient. Depth is configurable for slower hardware.

### 6.3 Threading

Synthesis is CPU-bound and runs in a bounded thread-pool executor — never on the event loop (HR-02).

---

## 7. Interruption (`playback.py`) — FR-120–122

### 7.1 Detection

While `SPEAKING`, VAD remains active. Sustained user speech (≥3 speech frames, ~96 ms) raises an interruption.

**Echo risk:** the microphone hears the assistant's own output. Mitigations, in order of preference:

1. OS-level acoustic echo cancellation where available (WASAPI AEC on Windows)
2. Raised VAD threshold while speaking
3. Ducking during a brief post-chunk window

Headphone use makes this moot; the design must not assume it.

### 7.2 Cancellation sequence

```
1. Publish voice.interrupt
2. Clear audio queue; stop playback stream           ← target <300ms (FR-120)
3. Clear chunk queue; signal the TTS worker to abort  (FR-121)
4. Cancel the orchestrator task (closes the LLM stream)
5. Mark turn INTERRUPTED; retain partial segments     (FR-122)
6. Auto-deny any pending approval for this turn       (Conversation LLD §8.1)
7. Return to LISTENING; lift wake suppression
```

Step 2 is the latency-critical path and must not await steps 3–6.

### 7.3 Budget

| Step | Target |
|---|---|
| VAD detection | ~96 ms |
| Queue clear + stream stop | < 50 ms |
| **Total to silence** | **< 300 ms** ✓ |

---

## 8. State Machine (`pipeline.py`)

```python
class VoiceState(StrEnum):
    IDLE = "idle"                  # wake word active
    CAPTURING = "capturing"        # recording utterance
    TRANSCRIBING = "transcribing"  # ASR running
    PROCESSING = "processing"      # orchestrator running
    SPEAKING = "speaking"          # TTS playback
```

| Transition | Trigger | Side effects |
|---|---|---|
| IDLE → CAPTURING | Wake word | Acknowledge <200 ms (FR-102); start buffer |
| CAPTURING → TRANSCRIBING | VAD end-point / 30 s cap | Stop capture |
| CAPTURING → IDLE | Silence timeout, no speech | Discard buffer |
| TRANSCRIBING → PROCESSING | Transcript non-empty | Create Turn |
| TRANSCRIBING → IDLE | Empty transcript | Inform user (FR-114) |
| PROCESSING → SPEAKING | First audio chunk ready | **Suppress wake word** (FR-103) |
| SPEAKING → IDLE | Playback complete | Lift suppression |
| SPEAKING → CAPTURING | **Interruption** | §7.2 sequence |
| any → IDLE | Error | Publish error; reset |

---

## 9. Latency Budget

| Stage | Measured / target |
|---|---|
| Wake-word detection | ~0.1 s *(Test 8 outstanding)* |
| VAD end-point | ~0.8 s *(Test 9 outstanding — reducible?)* |
| ASR (`base`, 3 s audio) | **0.75 s** measured |
| LLM prompt eval | **0.25 s** measured |
| LLM first tokens | **~0.4 s** measured |
| TTS first chunk (≤40 chars) | **~0.53 s** derived |
| **Total, wake word → first audio** | **≈2.8 s** |

> **Honest correction to the earlier estimate.** HLD §4.1 quoted ≈1.8–2.2 s, which *excluded VAD end-pointing*. Including the ~0.8 s end-point delay gives **≈2.8 s** — still inside the ≤3 s requirement (FR-223, BR-036), but with less margin than previously stated. **Test 9 should establish whether the 800 ms silence window can be shortened without truncating natural pauses.**

---

## 10. Test Matrix

| Test | Asserts | Ref |
|---|---|---|
| `test_abbreviation_no_split` | "Dr. Chen" stays whole | SR-06 |
| `test_eg_ie_no_split` | "e.g." / "i.e." stay whole | SR-06 |
| `test_decimal_no_split` | "3.5 inches" stays whole | SR-06 |
| `test_initials_no_split` | "J. R. R. Tolkien" stays whole | SR-06 |
| `test_acronym_no_split` | "U.S.A." stays whole | SR-06 |
| `test_terminator_at_buffer_end_defers` | Streaming deferral | §5.2 |
| `test_flush_resolves_deferral` | End of stream | §5.2 |
| **`test_first_chunk_under_40_chars`** | **TTFA rule** | §5.3 |
| **`test_no_chunk_exceeds_60_chars`** | **Knee avoidance** | §5.3 |
| `test_long_sentence_clause_split` | Clause priority | §5.4 |
| `test_no_split_inside_quotes` | §5.4 | — |
| `test_wake_suppressed_while_speaking` | Self-trigger | FR-103 |
| `test_vad_active_while_speaking` | Interruption possible | FR-120 |
| `test_interrupt_under_300ms` | Latency | FR-120 |
| `test_interrupt_retains_partial` | Partial kept | FR-122 |
| `test_interrupt_denies_approval` | Cross-module | Conv. LLD §8.1 |
| `test_empty_transcript_informs_user` | FR-114 | — |
| `test_asr_off_event_loop` | HR-02 | — |
| `test_tts_warm_on_start` | DD-105 | FR-720 |
| `test_correction_map_word_boundary` | "Pawnee, Oklahoma" preserved in context | §3.4 |

---

## 11. New Decisions Required

| Proposed ID | Decision |
|---|---|
| **DD-124** | **TTS chunk caps: first ≤40 chars, subsequent ≤55, hard max 60.** Basis: measured 5.4× cost knee at ~62 chars — the cap is a throughput requirement, not only a TTFA optimisation. Refines DD-106. |
| **DD-125** | Streaming segmenter uses an abbreviation table with **deferred boundary resolution**; off-the-shelf segmenters are unsuitable because they require complete text. |
| **DD-126** | STT vocabulary correction is **two-layer**: `initial_prompt` biasing plus a word-boundary-anchored correction map. |

---

## 12. Open Items

| # | Item | Disposition |
|---|---|---|
| 1 | Wake-word model training for "Hey Panini" | Pre-trained fallback until a custom model is trained |
| 2 | Echo cancellation approach on Windows | Test WASAPI AEC; fall back to threshold raising |
| 3 | Whether the 800 ms VAD end-point can be reduced | **Validation Test 9** — directly affects the latency budget |
| 4 | Correction map false positives ("Pawnee, Oklahoma") | Word-boundary anchoring; monitor in use |
| 5 | Re-measure "Panini" accuracy with a human voice | **Validation Test 7** — blocks confirming DO-007 |

---

## 13. Approval

| Role | Decision | Date |
|---|---|---|
| Project Owner | ☐ Approved ☐ Approved with changes ☐ Rejected | — |
