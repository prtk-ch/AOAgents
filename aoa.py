#!/usr/bin/env python3
"""Minimal always-on agent: triggers -> harness -> persistent workspace.

Mirrors the four pieces of the talk:
  triggers   cron-ish schedules + an inbox directory (events)
  harness    manual tool loop over the Messages API
  context    rebuilt per run from MEMORY.md + today's log (no live session)
  memory     plain files in workspace/, the agent edits them itself
"""
import collections, json, os, queue, re, subprocess, sys, threading, time, urllib.error, urllib.request
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).parent
WS = ROOT / "workspace"
STATE = ROOT / "state.json"
TRIGGERS = ROOT / "triggers.json"
def load_env(path=ROOT / ".env"):
    """Minimal .env reader: the config is three lines, not worth a dependency."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"\''))


load_env()
BASE_URL = os.environ.get("OPENAI_COMPAT_URL", "").rstrip("/")
API_KEY = os.environ.get("OPENAI_COMPAT_API_KEY", "")
MODEL = os.environ.get("OPENAI_COMPAT_MODEL", "")
ALLOW_SHELL = os.environ.get("AOA_ALLOW_SHELL") == "1"

# Manual firings (from ui.py) join the scheduler's own queue, so every run - scheduled,
# event, or hand-fired - is serialized through one worker. Two agents editing MEMORY.md
# at once is the kind of bug you only see in production.
JOBS: "queue.Queue[tuple[str, str]]" = queue.Queue()
RECENT = collections.deque(maxlen=50)      # what the UI shows; in-memory, logs are on disk

SYSTEM = """You are an always-on agent. You wake on a trigger, not on a human turn.

Your entire memory is the workspace directory. Nothing you learn survives unless you write it:
  MEMORY.md          durable facts and standing instructions. Keep it short; rewrite, don't pile up.
  log/YYYY-MM-DD.md  one line per action you took today.
  skills/*.md        procedures. If a skill applies to this trigger, read it before acting.
  outbox/            messages for the human, one file each.

Rules, in order:
1. If a skill applies, read it first with read_file.
2. Do the work with the tools. Do not describe doing it - call the tool.
3. Finish by calling append_file on today's log, exactly one line, starting with the
   HH:MM from the trigger header above, like: "14:05 replied to Acme about invoice 4411".
4. Update MEMORY.md only if something durable changed.

If there is nothing worth doing, append one log line saying so and stop. Doing nothing is a
valid outcome. Inventing work is not. Never write outside the workspace."""


# ---------- tools (all paths confined to workspace: inbox content is untrusted) ----------
def _safe(rel: str, write: bool = False) -> Path:
    p = (WS / rel).resolve()
    if not str(p).startswith(str(WS.resolve())):
        raise ValueError("path escapes workspace")
    if write and p.relative_to(WS.resolve()).parts[:1] == ("inbox",):
        # The inbox is where triggers come FROM. An agent that can write there can
        # manufacture its own triggers and loop forever - it did, on the first live run.
        raise ValueError("inbox is read-only: it is the trigger source, not a scratchpad")
    return p

def read_file(path): return _safe(path).read_text()[:40000]

def write_file(path, content):
    p = _safe(path, write=True); p.parent.mkdir(parents=True, exist_ok=True); p.write_text(content)
    return f"wrote {len(content)} bytes to {path}"

def append_file(path, content):
    p = _safe(path, write=True); p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a") as f: f.write(content.rstrip() + "\n")
    return f"appended to {path}"

def list_dir(path="."):
    p = _safe(path)
    return "\n".join(sorted(f.name + ("/" if f.is_dir() else "") for f in p.iterdir())) or "(empty)"

def shell(cmd):
    if not ALLOW_SHELL:
        return "shell is disabled (set AOA_ALLOW_SHELL=1 to enable)"
    # ponytail: subprocess in the workspace cwd, no sandbox. Untrusted triggers -> run the
    # whole harness in a container/VM, which is what the talk's Firecracker boxes are for.
    r = subprocess.run(cmd, shell=True, cwd=WS, capture_output=True,  # slopguard: ok this tool IS the shell
                       text=True, timeout=120)
    return (r.stdout + r.stderr)[:10000] or f"(exit {r.returncode}, no output)"

HANDLERS = {"read_file": read_file, "write_file": write_file, "append_file": append_file,
            "list_dir": list_dir, "shell": shell}

def _tool(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props,
                       "required": required, "additionalProperties": False}}}

S = {"type": "string"}
TOOLS = [
    _tool("read_file", "Read a file in the workspace.", {"path": S}, ["path"]),
    _tool("write_file", "Create or overwrite a workspace file. Cannot write to inbox/.", {"path": S, "content": S}, ["path", "content"]),
    _tool("append_file", "Append a line to a workspace file (use for logs).", {"path": S, "content": S}, ["path", "content"]),
    _tool("list_dir", "List a workspace directory.", {"path": S}, ["path"]),
    _tool("shell", "Run a shell command with the workspace as cwd.", {"cmd": S}, ["cmd"]),
]


# ---------- context: rebuilt from disk every run, never carried over ----------
def today_log() -> Path:
    return WS / "log" / f"{date.today().isoformat()}.md"

def build_context(trigger_name: str, prompt: str) -> str:
    mem = (WS / "MEMORY.md")
    log = today_log()
    skills = ", ".join(p.name for p in sorted((WS / "skills").glob("*.md"))) or "(none)"
    return (f"# Trigger: {trigger_name} at {datetime.now():%Y-%m-%d %H:%M}\n\n"
            f"## MEMORY.md\n{mem.read_text() if mem.exists() else '(empty)'}\n\n"
            f"## Today's log (tail)\n{''.join(log.read_text().splitlines(True)[-30:]) if log.exists() else '(empty)'}\n\n"
            f"## Available skills\n{skills}\n\n## Task\n{prompt}\n")


# ---------- harness: manual tool loop over an OpenAI-compatible endpoint ----------
def chat(messages, timeout=180, retries=2):
    """One POST to /chat/completions. urllib because the whole client is 15 lines."""
    body = json.dumps({"model": MODEL, "messages": messages, "tools": TOOLS,
                       "tool_choice": "auto", "max_tokens": 2048,
                       "temperature": 0.3}).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/chat/completions", data=body, method="POST",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {API_KEY}"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode()[:300]
            if e.code < 500 or attempt == retries:   # 4xx is our bug, not a blip
                raise RuntimeError(f"HTTP {e.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == retries:
                raise RuntimeError(f"{type(e).__name__}: {e}") from None
        time.sleep(2 ** attempt)


def run_agent(trigger_name: str, prompt: str, max_turns: int = 12) -> str:
    if not (BASE_URL and MODEL):
        # Dry runs go to their own file, never the agent's log: the log tail is part of the
        # next run's context, and a small model copies whatever format it finds there.
        append_file("dry-runs.log", f"{datetime.now():%H:%M} would have run: {trigger_name}")
        return "no OPENAI_COMPAT_URL/MODEL in .env; recorded in workspace/dry-runs.log"

    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": build_context(trigger_name, prompt)}]
    for _ in range(max_turns):
        r = chat(messages)
        choice = r["choices"][0]
        msg = choice["message"]
        calls = msg.get("tool_calls") or []
        if os.environ.get("AOA_DEBUG"):
            u = r.get("usage", {})
            print(f"   [debug] finish={choice['finish_reason']} "
                  f"calls={[c['function']['name'] for c in calls]} "
                  f"in={u.get('prompt_tokens')} out={u.get('completion_tokens')}", flush=True)

        # Echo the assistant turn back exactly as the server shaped it, minus the nulls
        # vLLM includes - some templates choke on a null content next to tool_calls.
        messages.append({k: v for k, v in
                         {"role": "assistant", "content": msg.get("content"),
                          "tool_calls": msg.get("tool_calls")}.items() if v})
        if not calls:
            # A small model often ends with empty content after its last tool call; the
            # work is real, it just said nothing about it. Point at the log, not at nothing.
            return (msg.get("content") or "").strip() or "(finished with no closing message - see today's log)"

        for c in calls:                              # a 12B model does emit several at once
            name = c["function"]["name"]
            try:
                args = json.loads(c["function"]["arguments"] or "{}")
                out = str(HANDLERS[name](**args))
            except Exception as e:                   # bad JSON, unknown tool, failed tool:
                out = f"ERROR {type(e).__name__}: {e}"   # tell the model, let it recover
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": out[:8000]})
    return "hit max_turns"


# ---------- triggers ----------
def load_state(): return json.loads(STATE.read_text()) if STATE.exists() else {}
def save_state(s): STATE.write_text(json.dumps(s, indent=2))


def paused() -> bool:
    return bool(load_state().get("_paused"))


def set_paused(on: bool):
    """Stored in state.json, so a pause survives a restart - stopping it should stay stopped."""
    st = load_state()
    st["_paused"] = bool(on)
    save_state(st)
    return on

def due(trig, last: float, now: float) -> bool:
    if "every" in trig:
        return now - last >= trig["every"]
    if "at" in trig:                                    # daily at HH:MM
        h, m = (int(x) for x in trig["at"].split(":"))
        fire = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0).timestamp()
        return now >= fire > last
    raise ValueError(f"trigger {trig['name']} needs 'every' or 'at'")

def pending_events():
    """Event triggers: any file dropped in workspace/inbox/, consumed exactly once."""
    inbox = WS / "inbox"
    for f in sorted(inbox.glob("*")):
        if f.is_file():
            yield f

def next_fire(trig, last: float, now: float) -> float:
    """When this trigger fires next, as a unix timestamp. For display only."""
    if "every" in trig:
        return last + trig["every"] if last else now
    h, m = (int(x) for x in trig["at"].split(":"))
    fire = datetime.now().replace(hour=h, minute=m, second=0, microsecond=0).timestamp()
    return fire if fire > last else fire + 86400


def tick(state, now, is_paused=False):
    fired = []
    for trig in json.loads(TRIGGERS.read_text()):
        if due(trig, state.get(trig["name"], 0), now):
            state[trig["name"]] = now          # advance even while paused, so resuming
            if not is_paused:                  # doesn't dump a backlog of missed ticks
                fired.append((trig["name"], trig["prompt"]))
    if is_paused:
        return fired    # leave the inbox untouched: mail waits for resume, it is not lost
    for f in pending_events():
        body = f.read_text()[:8000]
        done = WS / "inbox" / "processed"; done.mkdir(exist_ok=True)
        f.rename(done / f.name)                          # consume before running: no double-fire
        fired.append((f"event:{f.name}", f"An event arrived in the inbox:\n\n{body}"))
    return fired

def loop(interval=5):
    for d in ("log", "skills", "inbox", "outbox"):
        (WS / d).mkdir(parents=True, exist_ok=True)
    print(f"always-on: {MODEL} @ {BASE_URL or '(unconfigured)'}\n            workspace {WS}", flush=True)
    while True:
        state = load_state()
        fired = tick(state, time.time(), is_paused=bool(state.get("_paused")))
        while not JOBS.empty():                     # hand-fired from the UI
            fired.append(JOBS.get())
        for name, prompt in fired:
            print(f"[{datetime.now():%H:%M:%S}] FIRE {name}", flush=True)
            RECENT.appendleft({"at": datetime.now().isoformat(timespec="seconds"),
                               "name": name, "prompt": prompt, "out": "running...", "done": False})
            entry = RECENT[0]
            try:
                entry["out"] = run_agent(name, prompt).strip()
            except Exception as e:                  # one bad run must not kill the loop
                entry["out"] = f"{type(e).__name__}: {e}"
            entry["done"] = True
            print("   ->", entry["out"].replace("\n", "\n   "), flush=True)
        save_state(state)
        time.sleep(interval)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "run":
        loop()
    elif cmd == "once":                                  # one-shot: ./aoa.py once "do X"
        print(run_agent("manual", " ".join(sys.argv[2:]) or "Check in and log that you woke up."))
    elif cmd == "event":                                 # ./aoa.py event "text"  -> inbox
        p = WS / "inbox" / f"{int(time.time())}.txt"
        p.parent.mkdir(parents=True, exist_ok=True); p.write_text(" ".join(sys.argv[2:]))
        print(f"queued {p}")
    elif cmd == "pause":
        print("paused" if set_paused(True) else "")
    elif cmd == "resume":
        set_paused(False); print("resumed")
    elif cmd == "test":
        import test_aoa; test_aoa.main()
    else:
        sys.exit(__doc__)
