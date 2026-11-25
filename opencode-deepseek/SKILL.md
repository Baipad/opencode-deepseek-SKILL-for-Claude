---
name: opencode-deepseek
description: Delegate work to DeepSeek V4.1 Flash through opencode (OpenCode Go) - autonomous coding/edits/refactors that run unattended to completion, read-only code review, analysis, drafting, bulk or cheap tasks, image reading, second opinions. Use whenever the user mentions opencode, DeepSeek, deepseek v4.1 flash, or asks to hand a task to another model.
---

# opencode + DeepSeek

Run every call through the wrapper. It never hangs (stdin closed, total + idle timeouts, process tree killed), parses opencode's JSON events into one report, and salvages a failed run by continuing the same session.

```bash
OC="python -I $HOME/.claude/skills/opencode-deepseek/scripts/oc.py"
$OC --mode auto --dir "<PROJECT_DIR>" "<TASK>"
```

Provider: OpenCode Go (`opencode-go`, key in `~/.local/share/opencode/auth.json`). opencode 1.14.19.

## 1. Model (locked) — pick the mode

**Only `opencode-go/deepseek-v4.1-flash` is used.** This skill is locked to this model. The wrapper hard-codes it and has no model option; do not call opencode directly with another `-m`.

| Task | `--mode` |
|---|---|
| Implement / fix / refactor / create / run anything (default) | `auto` |
| Code review, audit, analysis, questions (no edits) | `review` |
| Read a screenshot / image (`-f img.png`) | `review` |

- V4.1 Flash: 1M context, 384K output, text + image input. ≈ $0.15 in / $0.60 out per 1M tokens; doubles weekdays UTC 01–04 and 06–10.
- No reasoning-effort setting: opencode defines no variants for this model, and `--variant` is silently ignored. For a hard task, give a more precise prompt, split it into steps, or ask it to reason step by step.

## 2. Modes (custom agents in `~/.config/opencode/agents/`)

| Mode | Agent | Can do | Blocked |
|---|---|---|---|
| `auto` | `ds-auto` | everything: edit, shell, web, other dirs, subagents | asking questions, endless loops, `.env`/keys via read or shell |
| `review` | `ds-review` | read, glob, grep, web, `git status/diff/log/show/blame`, `ls`, `wc` | any edit, any other shell command, `.env` via read or shell |
| `plan` / `build` | built-ins | plan: read-only edits but **runs shell freely**; build: everything | — |

Verified behaviour:
- Unattended runs never wait for approval: any permission "ask" is auto-rejected, the model gets an error and moves on.
- `ds-auto` told to "ask me first" decided itself and reported the assumption; it wrote code, ran tests, and ended with the report format below.
- `ds-review` could not write files or run non-allowlisted shell.
- Limit: the `grep` tool is checked by regex, not path, so it can still match inside `.env`. Secret protection is best-effort; do not point it at secret files.

`ds-auto` ends every answer with:
```
DONE | PARTIAL | FAILED
Files changed: ...
Verification: ...
Notes: ...
```

## 3. Wrapper options

| Option | Use |
|---|---|
| `--dir PATH` | project to work in (default: current dir). Always set it. |
| `-f FILE` | attach file or image, repeatable |
| `--prompt-file FILE` | task text from a file (for long or multi-line prompts) |
| `-s ID` / `-c` | continue a session by id (preferred) / the most recent one overall |
| `--timeout SEC` | total per attempt, default 1800 |
| `--idle SEC` | kill if no event, default 600. Raise it for long builds/test suites: tool events arrive only when a tool finishes. |
| `--retries N` | extra attempts, default 1. Retry continues the same session ("don't redo finished work"); fatal errors (bad model, auth) are not retried. |
| `--out FILE` | also save the report |
| `--verbose` | list every tool call |
| `--title TEXT` | session title |
| `--skip-permissions` | opencode auto-approve; not needed with `auto` |

Prompts over 6000 chars are attached as a file automatically (Windows command-line limit). Thai and other Unicode text work.

Exit code: `0` ok, `1` failed, `2` timeout. Raw events: the `log:` line at the end of each report.

## 4. Report format

```
=== opencode result ===
status: ok | failed (<reason>) | timeout (<reason>)
session: ses_...
model: ... | agent: ...
attempts: N | tool calls: N | tool errors: N | tokens: N | cost: $X
files changed: <paths or none>
tool errors: (only if any)
--- answer ---
<final answer>
log: <path to raw jsonl>
```

## 5. How to run tasks well

- Long jobs (big refactor, full test suite): run with Bash `run_in_background: true` and wait for the notification; do not poll.
- Parallel: several `review` runs, or `auto` runs on **different** directories, can run at once. Never two `auto` runs on the same files.
- One task per run. Name the files involved. State what "done" means ("tests pass", "list findings as file:line").
- Follow-ups go to the same session with `-s <session>` so context is kept and work is not repeated.

## 5b. Brief template (proven in real projects)

For anything beyond a one-liner, write the spec to a file and pass `--prompt-file`. A tight spec beats a long one: exact values, no padding. The user only needs to say what, where, and what "done" means; Claude writes the spec.

```
Task "<name>": <one-line goal>

## Facts (verified before the edit)
- current state, with file:line

## Do
1. specific step (numbers, function names, exact UI text)

## Rules
- files this task owns / files it must not touch
- do not break existing behaviour, add nothing beyond the task
- short comments, say only what the code does

## Verify
- build, tests (0 failures), plus e2e or a screenshot for UI work
- commit on a branch

## Report
DONE/PARTIAL/FAILED, files changed, file:line per item, test results, anything not done and why
```

Techniques that worked:
- **Shared rules file**: keep common rules in one file (e.g. `common.md`) and attach it to every task with `-f`.
- **Put known file:line in the spec**: lines already found by Claude or an earlier run make the run faster and more accurate.
- **Exact numbers and text**: never "make it nicer"; give values and the literal strings.
- **Verify with the real thing**: require an e2e run or screenshot, not only passing unit tests.
- **Design first for UI**: Claude decides the structure; DeepSeek implements it, it does not invent the design.
- **Parallel work**: give each task its own git worktree (`--dir <worktree>`), then run them at once. Long jobs: `--timeout 7200 --idle 1800`.
- **Fix rounds**: send corrections to the same session: `-s <id> --prompt-file round2.md`.
- **Do it yourself** when the user says so, or when the task is trivial: do not delegate.

## 6. After every run (required)

1. Check `status`. On `failed`/`timeout`, read the reason; continue with `-s <session>` and a narrower instruction, or raise `--idle`/`--timeout`. Do not start over from scratch when a session exists.
2. If `files changed` is not `none`: look at those files (and run the tests yourself if the change matters) before telling the user it works.
3. Treat the answer as a peer's opinion. DeepSeek's knowledge stops around 2025-05: verify claims about library versions, APIs and model names.
4. Report to the user: what was done, files changed, verification result, anything left.

## 7. Errors

| Symptom | Action |
|---|---|
| `Model not found` | usually not logged in: `opencode auth login`. Check ids with `"$LOCALAPPDATA/opencode/opencode-cli.exe" models opencode-go` |
| auth / 401 | ask the user to run `opencode auth login` (OpenCode Go key) |
| region / privacy error | DeepSeek needs workspace privacy region **Global** (Console → Settings → Privacy); tell the user, do not change it |
| usage limit (5-hour 20%, weekly 50% resets Mon 00:00 UTC, monthly) | tell the user; options: wait for the reset or enable Extra Usage in Console. Never fall back to another model. |
| `timeout (no activity ...)` on a long command | rerun with `-s <session>` and a larger `--idle` |
| `Agent not found` | `ds-auto.md` / `ds-review.md` missing from `~/.config/opencode/agents/`. The wrapper copies them from the skill's `agents/` folder when missing; it never overwrites, so delete an old copy to get the new one. |

Never put secrets (keys, tokens, `.env` contents) in prompts or attachments.
