#!/usr/bin/env python3
"""Run opencode non-interactively and print a clean report.

Never hangs: stdin closed, total + idle timeouts, process tree killed on timeout.
A failed run is continued in the same session instead of restarted.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

# Locked to one model on purpose; no override.
MODEL = "opencode-go/deepseek-v4.1-flash"
AGENTS = {"auto": "ds-auto", "review": "ds-review", "plan": "plan", "build": "build"}
EDIT_TOOLS = {"write", "edit", "patch", "multiedit"}
# Errors where retrying cannot help.
FATAL_MARKERS = ("ProviderModelNotFound", "ModelNotFound", "Model not found", "Unauthorized", "401",
                 "invalid api key", "AgentNotFound", "not found: agent")
LONG_PROMPT = 6000
LOG_DIR = Path(tempfile.gettempdir()) / "opencode-runs"


def find_bin():
    env = os.environ.get("OPENCODE_BIN")
    if env:
        return env
    local = Path(os.environ.get("LOCALAPPDATA", "")) / "opencode" / "opencode-cli.exe"
    if local.exists():
        return str(local)
    return shutil.which("opencode-cli") or shutil.which("opencode") or "opencode"


def ensure_agents():
    """Copy the bundled agents into opencode's agents folder if missing (no installer needed)."""
    src = Path(__file__).resolve().parent.parent / "agents"
    dst = Path.home() / ".config" / "opencode" / "agents"
    if not src.is_dir():
        return
    dst.mkdir(parents=True, exist_ok=True)
    for f in src.glob("*.md"):
        if not (dst / f.name).exists():
            shutil.copyfile(f, dst / f.name)


def kill_tree(proc):
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        proc.kill()


def short(value, n=100):
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    s = s.replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


class Run:
    def __init__(self):
        self.session = None
        self.steps = []          # list of text chunks per step
        self.tools = []          # (tool, summary, status, error)
        self.files = []
        self.cost = 0.0
        self.tokens = 0
        self.finish = None
        self.events = 0
        self.stderr = ""
        self.status = "failed"
        self.reason = ""

    def feed(self, ev):
        self.events += 1
        self.session = ev.get("sessionID") or self.session
        part = ev.get("part") or {}
        t = ev.get("type")
        if t == "step_start":
            self.steps.append([])
        elif t == "text":
            if not self.steps:
                self.steps.append([])
            self.steps[-1].append(part.get("text", ""))
        elif t == "tool_use":
            state = part.get("state") or {}
            inp = state.get("input") or {}
            tool = part.get("tool", "?")
            path = inp.get("filePath") or inp.get("path")
            summ = inp.get("command") or path or inp.get("pattern") or inp.get("url") or inp
            err = state.get("error") if state.get("status") == "error" else None
            self.tools.append((tool, short(summ), state.get("status"), err))
            if tool in EDIT_TOOLS and path and state.get("status") == "completed" and path not in self.files:
                self.files.append(path)
        elif t == "step_finish":
            self.cost += part.get("cost") or 0
            self.tokens += (part.get("tokens") or {}).get("total") or 0
            self.finish = part.get("reason")
        elif t == "error":
            self.reason = short(ev.get("error") or ev, 300)

    def answer(self):
        for chunk in reversed(self.steps):
            text = "".join(chunk).strip()
            if text:
                return text
        return ""


def build_cmd(a, prompt, session, files):
    cmd = [find_bin(), "run", "--format", "json", "-m", MODEL,
           "--agent", AGENTS.get(a.mode, a.mode), "--dir", a.dir]
    if session:
        cmd += ["-s", session]
    elif a.cont:
        cmd += ["-c"]
    if a.title and not session:
        cmd += ["--title", a.title]
    for f in files:
        cmd += ["-f", f]
    if a.skip_permissions:
        cmd += ["--dangerously-skip-permissions"]
    # "--" stops a prompt starting with "-" being read as a flag.
    cmd += ["--", prompt]
    return cmd


def execute(cmd, run, total_timeout, idle_timeout, log):
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, cwd=None)
    last = [time.time()]
    err_buf = []

    def read_out():
        for raw in proc.stdout:
            last[0] = time.time()
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            log.write(line + "\n")
            try:
                run.feed(json.loads(line))
            except json.JSONDecodeError:
                err_buf.append(line)

    def read_err():
        for raw in proc.stderr:
            err_buf.append(raw.decode("utf-8", "replace"))

    threads = [threading.Thread(target=read_out, daemon=True),
               threading.Thread(target=read_err, daemon=True)]
    for th in threads:
        th.start()
    start = time.time()
    timed_out = None
    while proc.poll() is None:
        now = time.time()
        if now - start > total_timeout:
            timed_out = f"total timeout {total_timeout}s"
        elif now - last[0] > idle_timeout:
            timed_out = f"no activity for {idle_timeout}s"
        if timed_out:
            kill_tree(proc)
            break
        time.sleep(0.5)
    for th in threads:
        th.join(timeout=5)
    run.stderr = "".join(err_buf)[-2000:]
    if timed_out:
        run.status, run.reason = "timeout", timed_out
    elif run.finish == "stop" and run.answer():
        run.status = "ok"
    else:
        run.status = "failed"
        if not run.reason:
            tail = run.stderr.strip().splitlines()
            run.reason = (tail[0] if tail else f"finish={run.finish}, events={run.events}")[:300]
    return proc.returncode


def is_fatal(run):
    blob = run.reason + run.stderr
    return any(m.lower() in blob.lower() for m in FATAL_MARKERS)


def report(runs, a):
    last = runs[-1]
    files, tools, cost, tokens = [], [], 0.0, 0
    for r in runs:
        files += [f for f in r.files if f not in files]
        tools += r.tools
        cost += r.cost
        tokens += r.tokens
    errors = [t for t in tools if t[2] == "error"]
    lines = [
        "=== opencode result ===",
        f"status: {last.status}" + (f" ({last.reason})" if last.status != "ok" else ""),
        f"session: {last.session or '-'}",
        f"model: {MODEL} | agent: {AGENTS.get(a.mode, a.mode)}",
        f"attempts: {len(runs)} | tool calls: {len(tools)} | tool errors: {len(errors)}"
        f" | tokens: {tokens} | cost: ${cost:.4f}",
        "files changed: " + (", ".join(files) if files else "none"),
    ]
    if a.verbose and tools:
        lines.append("tools:")
        lines += [f"  {t[2] or '?':9} {t[0]:8} {t[1]}" for t in tools]
    if errors:
        lines.append("tool errors:")
        lines += [f"  {t[0]}: {short(t[3] or t[1], 160)}" for t in errors[:8]]
    if last.status != "ok" and "Model not found" in (last.reason + last.stderr):
        lines.append("hint: not logged in to OpenCode Go (or model unavailable). Run: opencode auth login")
    lines += ["--- answer ---", last.answer() or "(no answer text)"]
    return "\n".join(lines)


def main():
    p = argparse.ArgumentParser(description="Run opencode (DeepSeek via OpenCode Go) unattended.")
    p.add_argument("prompt", nargs="*", help="task text")
    p.add_argument("--prompt-file", help="read the task text from this file")
    p.add_argument("--mode", default="auto", help="auto | review | plan | build | <agent id>")
    p.add_argument("--dir", default=os.getcwd(), help="project directory to work in")
    p.add_argument("-f", "--file", action="append", default=[], help="attach file/image (repeatable)")
    p.add_argument("-s", "--session", help="continue this session id")
    p.add_argument("-c", "--continue", dest="cont", action="store_true", help="continue last session")
    p.add_argument("--title", help="session title")
    p.add_argument("--timeout", type=int, default=1800, help="total seconds per attempt")
    # Tool events arrive only when a tool finishes, so a long build/test looks idle.
    p.add_argument("--idle", type=int, default=600, help="kill if no event for this many seconds")
    p.add_argument("--retries", type=int, default=1, help="extra attempts after a failure")
    p.add_argument("--out", help="also write the report to this file")
    p.add_argument("--verbose", action="store_true", help="list every tool call")
    p.add_argument("--skip-permissions", action="store_true", help="auto-approve every permission")
    a = p.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    prompt = Path(a.prompt_file).read_text(encoding="utf-8") if a.prompt_file else " ".join(a.prompt)
    prompt = prompt.strip()
    if not prompt:
        p.error("no prompt given")
    a.dir = str(Path(a.dir).resolve())
    if not Path(a.dir).is_dir():
        p.error(f"--dir not found: {a.dir}")

    files = [str(Path(f).resolve()) for f in a.file]
    if len(prompt) > LONG_PROMPT:
        # Windows command lines are capped; ship long prompts as an attachment.
        tmp = Path(tempfile.mkstemp(prefix="oc-task-", suffix=".md")[1])
        tmp.write_text(prompt, encoding="utf-8")
        files.append(str(tmp))
        prompt = f"Your full task is in the attached file {tmp.name}. Follow it exactly."

    ensure_agents()
    LOG_DIR.mkdir(exist_ok=True)
    log_path = LOG_DIR / f"run-{time.strftime('%Y%m%d-%H%M%S')}.jsonl"
    runs = []
    session = a.session
    with open(log_path, "w", encoding="utf-8") as log:
        for attempt in range(a.retries + 1):
            run = Run()
            if attempt == 0:
                cmd = build_cmd(a, prompt, session, files)
            else:
                prev = runs[-1]
                session = prev.session or session
                if session:
                    # Salvage: keep the work done so far and finish the rest.
                    msg = (f"The previous attempt stopped early ({prev.reason}). Continue from "
                           "where you left off and finish the original task. Do not redo work "
                           "that is already done. If a command ran too long, narrow it or add "
                           "its own timeout. End with the required report.")
                    cmd = build_cmd(a, msg, session, [])
                else:
                    time.sleep(5 * attempt)
                    cmd = build_cmd(a, prompt, None, files)
            execute(cmd, run, a.timeout, a.idle, log)
            runs.append(run)
            if run.status == "ok" or is_fatal(run):
                break

    out = report(runs, a) + f"\nlog: {log_path}"
    print(out)
    if a.out:
        Path(a.out).write_text(out, encoding="utf-8")
    sys.exit({"ok": 0, "timeout": 2}.get(runs[-1].status, 1))


if __name__ == "__main__":
    main()
