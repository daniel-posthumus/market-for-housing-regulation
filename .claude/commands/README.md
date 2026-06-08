# Commands

Project-scoped slash commands. Each `.md` file in this folder becomes a
command invokable as `/<filename>` (without the extension) inside Claude
Code while working in this repo.

The file body is the prompt. Frontmatter is optional but useful — e.g.:

```markdown
---
description: Summarize today's work in .claude/logs/
---

Read the most recent file in .claude/logs/ and produce a 5-bullet summary.
```
