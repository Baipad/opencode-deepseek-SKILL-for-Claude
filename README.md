# opencode-deepseek skill for Claude Code

Lets Claude hand tasks to DeepSeek V4.1 Flash through opencode (OpenCode Go): autonomous coding that runs to the end, or read-only review. Never hangs, one clean report per run.

## Needs
- Python 3, Claude Code
- opencode (https://opencode.ai), logged in to OpenCode Go (`opencode auth login`). Your own subscription; this package contains no key.

## Install
Copy the `opencode-deepseek` folder into `~/.claude/skills/` (Windows: `C:\Users\<you>\.claude\skills\`). Restart Claude Code. That's it.

On first run the script copies its two agents (`ds-auto`, `ds-review`) into `~/.config/opencode/agents/` if they are missing. Claude may ask once to allow running the script; approve it.

## Use
Ask Claude: "have deepseek implement X" or "use deepseek to review this project". Or run directly:
```
python -I ~/.claude/skills/opencode-deepseek/scripts/oc.py --mode auto --dir "<project>" "<task>"
python -I ~/.claude/skills/opencode-deepseek/scripts/oc.py --mode review --dir "<project>" "<question>"
```

## Modes
- `auto`: edits files, runs shell and tests, never asks questions.
- `review`: read-only. Cannot edit or run arbitrary shell.

## Notes
- Locked to `opencode-go/deepseek-v4.1-flash`. That model has no effort setting.
- `.env` and key files are blocked for read and shell; the grep tool can still search them. Don't point it at folders holding secrets.
- DeepSeek's knowledge ends around mid-2025: check library versions and API claims, and review its code before shipping.
- Tested on Windows only.
- Uninstall: delete the skill folder and the two `ds-*.md` files in `~/.config/opencode/agents/`.
