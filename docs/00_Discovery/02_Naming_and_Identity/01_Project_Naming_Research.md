# Project Panini — Naming Research & Identity Document

**Document Type:** Discovery — Naming & Identity  
**Phase:** 00 — Discovery  
**Status:** FINALIZED  
**Decision Date:** 2026-09-08  
**Author:** Project Owner  

---

## 1. Objective

To select a project name and assistant identity that is:
- Culturally meaningful and historically grounded
- Acoustically suited for wake-word detection
- Aligned with the assistant's core capabilities (voice, intelligence, retrieval, action)
- Distinctive and unambiguous in everyday use
- Professional enough for documentation and future extension

---

## 2. Naming Candidates Evaluated

Two primary candidates were researched and compared:

### Candidate A: NARAD

**Historical Identity:** Narad Muni — the divine cosmic messenger in Hindu mythology. A celestial traveler across realms (*Triloka*), bearer of timely intelligence, communicator between gods and mortals.

**Metaphorical Fit:**
- The Scout & Communicator: retrieves data from external systems, speaks and delivers
- Proactive, expressive, conversational
- Associated with sound, speech, news, multi-realm agility

**Wake-word Analysis (`"Hey Narad"`):**
- Syllables: 2 — quick, punchy, low-latency
- Acoustic profile: Strong consonants (N, R, D) — easy for OpenWakeWord / Porcupine to detect, minimal false triggers
- Ambiguity: None — not a common English word

**Acronym:**  
> **N.A.R.A.D.** — Neural Agent for Retrieval, Action & Display

**Avatar Persona:** Dynamic, expressive, proactive companion — excels as a *presenter* (images, news, emails, video windows).

---

### Candidate B: PANINI ← **SELECTED**

**Historical Identity:** Pāṇini (4th century BCE) — the ancient Indian grammarian and mathematician. Author of the *Aṣṭādhyāyī*, the world's first formal generative grammar — a rule-based system of ~4,000 sutras that describes the entire Sanskrit language algorithmically. Considered the father of formal linguistics and a forerunner of computer science's formal language theory.

**Metaphorical Fit:**
- The Brain & Parser: understands complex syntax, parses natural language, executes structured logic
- Elegant, calm, scholarly, precise, analytical advisor
- Associated with grammar, algorithmic structure, logic, and precision
- Direct lineage to modern NLP, LLM tokenization, and formal language theory — exactly what this assistant is built on

**Wake-word Analysis (`"Hey Panini"`):**
- Syllables: 3 — rhythmic, melodic, pleasant from TTS
- Acoustic profile: Soft plosive (P) + repetitive nasals (N) — friendly, approachable tone
- Ambiguity: Shares name with the Italian pressed sandwich in casual Western contexts — minor, non-technical

**Acronym:**  
> **P.A.N.I.N.I.** — Personal Assistant for Natural Information & Neural Interaction

**Avatar Persona:** Calm, scholarly, precise advisor — excels as a *compiler and processor* (code, structured data, language, analysis).

---

## 3. Side-by-Side Comparison

| Dimension | NARAD | PANINI |
|---|---|---|
| Historical identity | Divine messenger, cosmic communicator | Father of formal grammar and algorithmic linguistics |
| Core metaphor | Scout & Communicator | Brain & Parser |
| Wake-word syllables | 2 (`Hey Na-rad`) | 3 (`Hey Pa-ni-ni`) |
| Acoustic clarity | High (strong consonants) | High (melodic, no close homophones) |
| Everyday ambiguity | None | Minor (sandwich) |
| Avatar persona | Dynamic, expressive | Elegant, scholarly |
| Acronym | Neural Agent for Retrieval, Action & Display | Personal Assistant for Natural Information & Neural Interaction |
| CS heritage | Communicator | Formal language theory — closer to LLM roots |
| Project-name cadence | Punchy, tactical | Intellectual, classic |

---

## 4. Decision

**Selected Name: PANINI**  
**Full Project Name: Project Panini**  
**Assistant Name: Panini**  
**Wake Word: "Hey Panini"**  
**Acronym: P.A.N.I.N.I.** — *Personal Assistant for Natural Information & Neural Interaction*  

### Rationale for Selection

1. **Heritage alignment:** Pāṇini's *Aṣṭādhyāyī* is a generative rule system — the intellectual ancestor of every formal grammar, compiler, and language model. Naming an AI assistant after him is not just poetic; it is technically accurate.

2. **Capability framing:** The assistant's deepest strengths are natural language understanding, structured output rendering, and logical tool execution — all Pāṇini's domain, not just message delivery.

3. **Distinctiveness:** In the AI assistant landscape, no known product is named Panini. It stands out.

4. **Scalability:** As the project grows beyond personal use (potential cloud extension), "Project Panini" reads as a serious product name in documentation, presentations, and handovers.

5. **Dual-layer option:** If a sub-component is needed for the voice/communication layer specifically, "Narad" can be retained as the name of the **voice interface agent** within Project Panini (optional, Phase 2 decision).

---

## 5. Brand Identity Guidelines (Preliminary)

| Element | Value |
|---|---|
| Project name | Project Panini |
| Assistant name | Panini |
| Wake word | "Hey Panini" |
| Acronym | P.A.N.I.N.I. |
| Tone | Intelligent, calm, precise, helpful |
| Avatar style | Scholarly but approachable — VRM 3D primary (Live2D optional) |
| Color palette | To be defined in UI/UX phase |
| Tagline (draft) | *"The intelligence that listens, understands, and acts."* |

---

## 6. Folder & File Naming Convention

All project folders, documents, and code artifacts will use `panini` as the canonical identifier:

```
Project Panini/          ← Root folder
├── 00_Discovery/
├── 01_BRD/
├── 02_Solution_Design/
...
backend/
    panini_server.py
    panini_config.yaml
frontend/
    PaniniApp.tsx
```

---

## 7. Decision Log

| Date | Decision | Made By |
|---|---|---|
| 2026-09-08 | Two candidates (NARAD, PANINI) evaluated | Project Owner + Claude |
| 2026-09-08 | **PANINI selected as final project name** | Project Owner |
