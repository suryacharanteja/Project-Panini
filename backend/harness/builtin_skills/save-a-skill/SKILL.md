---
name: save-a-skill
description: Turn a task that just succeeded into a reusable skill when the person asks to save or repeat it.
---

1. Summarise the steps that actually worked in this conversation (not the ones that failed).
2. Generalise names, dates and paths into placeholders the person fills in next time.
3. Draft a lowercase-hyphenated name and a one-sentence description of when to use it.
4. Show the draft to the person and ask for approval with ask_user.
5. Only after approval, call create_skill with the name, description and numbered instructions.
