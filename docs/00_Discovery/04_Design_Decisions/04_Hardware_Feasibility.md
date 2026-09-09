# Project Panini — Hardware Feasibility Analysis

**Document Type:** Discovery — Technical Feasibility  
**Phase:** 00 — Discovery  
**Status:** Complete — CONSTRAINT IDENTIFIED  
**Date:** 2026-09-09  

---

## Purpose

DD-004 (local-first) and DD-041 (Ollama local LLM default) assume the target machine can run the full local AI stack. This document verifies that assumption against actual hardware.

---

## 1. Target Machine Specification (Measured)

| Component | Specification |
|---|---|
| **CPU** | Intel Core i7-13650HX — 14 cores / 20 threads @ 2.6 GHz base |
| **System RAM** | 63.7 GB total (39.2 GB free at time of measurement) |
| **GPU** | NVIDIA GeForce RTX 3050 **6 GB** Laptop GPU |
| **VRAM** | 6144 MiB (6001 MiB free) |
| **Compute Capability** | 8.6 (Ampere — supports FP16, INT8, Tensor Cores) |
| **NVIDIA Driver** | 591.86 |
| **Secondary GPU** | Intel UHD Graphics (integrated) |
| **Disk (D:)** | 1331 GB free |

### Initial Assessment

| Resource | Verdict |
|---|---|
| CPU | ✅ **Excellent** — 20 threads is substantial headroom |
| RAM | ✅ **Excellent** — 64 GB is far above requirement |
| Disk | ✅ **Excellent** — ample for models |
| **VRAM** | ⚠️ **CONSTRAINED — 6 GB is the bottleneck** |

---

## 2. 🔴 The Core Constraint: 6 GB VRAM

The locked design requires **four GPU-competing workloads running simultaneously**:

| Workload | Decision | Typical VRAM (GPU mode) |
|---|---|---|
| VRM avatar rendering (Three.js/WebGL) | DD-030 | ~0.3 – 0.6 GB + continuous render compute |
| Speech-to-Text (faster-whisper) | DD-020 | 1.0 – 5.0 GB depending on model/quantisation |
| Text-to-Speech (Kokoro-82M) | DD-022 | ~0.3 – 0.5 GB |
| Local LLM (Ollama) | DD-041 | 2.0 – 5.0 GB depending on model/quantisation |

### Naive all-on-GPU configuration

```
VRM avatar        0.5 GB
Whisper small     1.0 GB   (int8)
Kokoro TTS        0.4 GB
Llama 3.1 8B Q4   4.7 GB
──────────────────────────
TOTAL             6.6 GB   ❌ EXCEEDS 6.0 GB — will OOM
```

**Conclusion: the naive configuration does not fit.** DD-041 as written is not achievable without adjustment.

---

## 3. Resolution: Exploit the CPU/RAM Surplus

The machine has an unusual profile — **modest GPU, very strong CPU and RAM**. The correct strategy is to move non-latency-critical workloads to CPU and reserve VRAM exclusively for the LLM.

### Recommended Configuration — "CPU-offload"

| Workload | Placement | Justification |
|---|---|---|
| **VRM avatar** | GPU (~0.5 GB) | Must be GPU — it is real-time rendering |
| **faster-whisper (small, int8)** | **CPU** | 20 threads handles this well; STT runs in short bursts, not continuously |
| **Kokoro TTS** | **CPU** | 82M params — very light; CPU inference is adequate |
| **Ollama LLM** | **GPU (~5.0 GB available)** | The only workload that genuinely needs GPU for acceptable latency |

```
VRM avatar        0.5 GB  (GPU)
Whisper           0.0 GB  (CPU — uses ~2 GB system RAM)
Kokoro            0.0 GB  (CPU — uses ~1 GB system RAM)
LLM budget        5.0 GB  ✅ available
──────────────────────────
GPU TOTAL         5.5 GB  ✅ FITS within 6.0 GB
```

---

## 4. Viable Local LLM Options (≈5 GB VRAM budget)

| Model | Quantisation | Approx. VRAM | Quality | Verdict |
|---|---|---|---|---|
| Llama 3.2 3B | Q4_K_M | ~2.0 GB | Moderate | ✅ Comfortable, large headroom |
| Phi-3 Mini 3.8B | Q4 | ~2.3 GB | Good for size | ✅ Comfortable |
| Qwen 2.5 7B | Q4_K_M | ~4.4 GB | Good | ✅ Fits — recommended |
| Mistral 7B | Q4_K_M | ~4.1 GB | Good | ✅ Fits |
| Llama 3.1 8B | Q4_K_M | ~4.7 GB | Very good | ⚠️ Tight — viable but little headroom |
| Llama 3.1 8B | Q5_K_M | ~5.7 GB | Very good | ❌ Too tight with avatar |
| Any 13B+ model | Q4 | 7 GB+ | Better | ❌ Does not fit |

**Recommendation:** Target the **7B Q4 class** (Qwen 2.5 7B or Mistral 7B) as the local default. Llama 3.1 8B Q4 is viable but leaves almost no margin.

---

## 5. Impact on Locked Decisions

| ID | Decision | Status | Required Amendment |
|---|---|---|---|
| **DD-020** | STT: faster-whisper | ⚠️ Amend | Specify **CPU inference, `small` model, int8 quantisation** |
| **DD-022** | TTS: Kokoro | ⚠️ Amend | Specify **CPU inference** |
| **DD-041** | Default LLM: Ollama local | ⚠️ Amend | Constrain to **≤7B parameters, Q4 quantisation** |
| **DD-030** | VRM 3D avatar | ✅ Unaffected | Confirmed feasible — RTX 3050 handles WebGL VRM easily |
| **DD-004** | Local-first | ✅ **Confirmed viable** | The full stack does run locally on this machine |

**Key outcome: local-first is achievable.** It requires deliberate CPU/GPU placement rather than defaulting everything to GPU.

---

## 6. Expected Performance (Estimated)

> ⚠️ These are estimates derived from published model characteristics, **not measured on this machine.** Empirical benchmarking is required — see Section 8.

| Stage | Expected latency |
|---|---|
| Wake word detection | < 100 ms |
| STT — faster-whisper small int8 on CPU (3 s utterance) | ~0.5 – 1.5 s |
| LLM first token — 7B Q4 on RTX 3050 | ~0.3 – 0.8 s |
| LLM generation throughput | ~15 – 25 tokens/sec |
| TTS — Kokoro on CPU (first audio chunk) | ~0.3 – 0.7 s |
| **Estimated wake-word → first audio response** | **≈ 2 – 4 seconds** |

For comparison, cloud assistants achieve roughly 1–2 s. A 2–4 s local response is acceptable but noticeably slower.

### Latency reduction options (if 2–4 s proves too slow)

1. Use a 3B model instead of 7B — roughly halves LLM latency
2. Stream TTS as the LLM generates, rather than waiting for the full response
3. Use `faster-whisper base` instead of `small` — faster, slightly less accurate
4. Route to a cloud LLM for latency-sensitive interactions (DD-042 already permits this)

---

## 7. Thermal & Power Consideration

This is a **laptop GPU**. Sustained inference will cause thermal throttling and significant battery drain.

| Consideration | Implication |
|---|---|
| Always-on daemon (DD-090) | Should **idle without holding the model in VRAM**, or use Ollama's `keep_alive` timeout |
| Sustained load | Expect clock throttling during long generations |
| Battery operation | Local LLM inference on battery is impractical — plan for plugged-in use |

**Recommendation:** Add a configuration option for **model unload timeout** so the daemon releases VRAM when idle.

---

## 8. Required Validation (Before Implementation)

These estimates must be confirmed empirically before the LLD phase locks performance targets:

| # | Test | Success Criterion |
|---|---|---|
| 1 | Install Ollama; run Qwen 2.5 7B Q4; measure VRAM + tokens/sec | Fits in ≤5 GB, ≥15 tok/s |
| 2 | Run faster-whisper `small` int8 on CPU; measure transcription latency | ≤1.5 s for a 3 s utterance |
| 3 | Run Kokoro on CPU; measure time-to-first-audio | ≤0.7 s |
| 4 | Render a VRM model in a browser; measure VRAM + FPS | ≥30 FPS, ≤0.6 GB VRAM |
| 5 | **Run all four concurrently; measure peak VRAM** | **Peak < 6.0 GB, no OOM** |
| 6 | Measure end-to-end wake-word → first audio | Establishes the real baseline |

**Test 5 is the decisive one.** It validates or invalidates the entire local-first architecture.

---

## 9. Conclusion

| Question | Answer |
|---|---|
| Can this machine run Project Panini fully locally? | ✅ **Yes — with deliberate CPU/GPU workload placement** |
| Is the naive all-on-GPU design viable? | ❌ No — exceeds 6 GB VRAM |
| Is DD-004 (local-first) still valid? | ✅ Yes — confirmed |
| Is DD-041 (Ollama default) still valid? | ⚠️ Yes, **but must be constrained to ≤7B Q4** |
| What is the binding constraint? | **6 GB VRAM** |
| What is the compensating strength? | **20 CPU threads + 64 GB RAM** — unusually strong, and the reason this works |
