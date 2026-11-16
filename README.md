# AOAgents — a minimal always-on agent on self-hosted Gemma

The architecture from "Stanford CS146S: How always-on agents work" (leerob), running on your
own vLLM box. **No SDK, no dependencies** — stdlib `urllib` against an OpenAI-compatible endpoint.

    cp .env.example .env     # point it at your own vLLM host
    python3 ui.py            # scheduler + test console on http://127.0.0.1:8787
    python3 aoa.py run       # headless
    python3 aoa.py once "summarise my open threads"
    python3 aoa.py event "vendor wants a reply today"
    python3 test_aoa.py      # self-check, no model calls
    python3 aoa.py pause     # stop every automatic firing (survives restart)
    python3 aoa.py resume
    pkill -f ui.py           # the hammer, if a stray scheduler is ticking

Without `OPENAI_COMPAT_URL`/`MODEL` the loop still runs and records each firing in
`workspace/dry-runs.log`, so the scheduling half is testable with the GPU off.
Test plan: [TESTING.md](TESTING.md).

## The four pieces

| Talk | Here |
|---|---|
| triggers (cron, email, Slack) | `triggers.json` (`every` seconds / `at` HH:MM) + any file dropped in `workspace/inbox/` |
| server-side harness | `run_agent()` — tool loop over `POST /v1/chat/completions` |
| context engineering | `build_context()` rebuilds the prompt per run from MEMORY.md + today's log tail + skill list. No carried-over session |
| memory | plain files in `workspace/`: `MEMORY.md`, `log/YYYY-MM-DD.md`, `skills/*.md`, `outbox/` |

Each firing is a fresh context. The only thing that crosses runs is what the agent wrote to disk.

## Stopping it

The console header has **Pause triggers** and **Shut down**. Pause is stored in `state.json`, so
it stays paused across restarts, and it means exactly this:

- no scheduled runs, and **inbox mail is left in place** — it waits, it is not lost or consumed;
- the trigger clocks still advance, so resuming does **not** dump a backlog of missed ticks;
- **Fire now** and one-shots still work — pausing stops the automatic half, not your own hands.

`heartbeat` ships at `every: 900` (15 min). It was 60 while testing; that is 1,440 runs a day
against your own GPU, which is fine for an afternoon and wrong as a standing config.

## The endpoint this was built against

`google/gemma-4-12B-it` on vLLM 0.28 — 64K context, 4-bit (w4a16) quantized, self-hosted.
Verified working:
a `system` role, native `tool_calls`, and the `role: "tool"` result round-trip. That last part is
not guaranteed on a self-hosted stack: **vLLM only emits `tool_calls` when it was started with
`--enable-auto-tool-choice --tool-call-parser <parser>`.** If your server starts *describing* tool calls in prose
instead of making them, that missing flag is why.

## What changed coming off Anthropic

- **The advisor is gone.** It was a server-side Anthropic feature pairing a cheap executor with a
  smarter adviser model. One endpoint, one model, no equivalent — so there is no "phone a senior"
  step. If you later host something bigger, the cheapest version is a second `chat()` call to that
  model with the plan, before acting.
- **No thinking / effort / caching.** No adaptive thinking, no prompt-cache breakpoints (vLLM does
  its own prefix caching — `created_cache_tokens` in the response shows it working).
- **`max_tokens: 2048`, `temperature: 0.3`** per call, and `max_turns: 12`. A 12B model rambles at
  higher temperature and loops on redundant tool calls; both numbers are tuning knobs in `chat()`.
- **The prompt got blunter.** Numbered rules, an explicit log-line format with an example. A 12B
  model needs the shape shown, not described — it dropped timestamps until the format was spelled out.

## Two bugs the first live runs found

- **The agent triggered itself.** It wrote a file into `inbox/`, the scheduler read that as a new
  event, and fired again — `event:event:...` in the log. `inbox/` is now **input-only** (`_safe(...,
  write=True)` rejects it). An agent that can write to its own trigger source is an infinite loop
  waiting to happen.
- **It copied the log's format, including junk.** Dry-run lines said `[no credentials] would have
  run: x`, so the model's own log line came out as `[no credentials] The Gemma harness works.`
  The log tail is *context*; whatever is in it is a template. Dry runs now go to a separate file.

## Deliberately skipped

- **No sandbox.** `shell` is off unless `AOA_ALLOW_SHELL=1`, and file tools are confined to
  `workspace/`. Real always-on agents run a VM per session, because trigger content is untrusted.
- **No daemon.** `nohup python3 ui.py &`, or a launchd plist, to survive logout.
- **No cron parser.** `every N` and `at HH:MM`. First boot after the daily time catches up once.
- **No queue.** The inbox directory is the queue; a file is moved to `inbox/processed/` before the
  run, so a crash drops that event rather than replaying it forever.
