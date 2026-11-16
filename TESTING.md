# Testing the always-on agent

    python3 ui.py        →  http://127.0.0.1:8787

That one command runs the scheduler *and* the console, so there's no daemon to manage.
Localhost only — the page fires arbitrary prompts.

The console: triggers with a live countdown and a **Fire now** button, an inbox drop box, a
one-shot prompt, the run feed, MEMORY.md, today's log, and a file viewer for `skills/`,
`inbox/`, `outbox/`.

---

## Phase 1 — endpoint off (proves the scheduling half, no GPU)

Comment out `OPENAI_COMPAT_URL` in `.env` and everything but the model call still works; each
firing is recorded in `workspace/dry-runs.log`. The header says `no endpoint in .env`.

1. **Scheduled trigger.** Open the console, watch `heartbeat` count down from 60. At zero a run
   appears in the feed and a line lands in today's log.
2. **Manual firing.** Hit **Fire now** on `morning-brief`. It appears as `morning-brief (manual)`
   — the manual path shares the scheduler's queue, so runs stay serialized.
3. **Event trigger.** Type into *Fire an event* → **Drop in inbox**. Within 3s a run named
   `event:<ts>-ui.txt` fires, and the file moves to `workspace/inbox/processed/`.
4. **Consumed exactly once.** Fire the same event twice; the feed shows two runs, and
   `inbox/` is empty while `inbox/processed/` holds both. No replay.
5. **Restart safety.** Ctrl-C, restart `ui.py`. `heartbeat`'s countdown resumes from
   `state.json` rather than firing immediately — `morning-brief` catches up once if 08:30 passed.
6. **Pause.** Hit **Pause triggers**: the banner appears, countdowns dim, nothing fires. Drop an
   event while paused — it stays in `inbox/` instead of being consumed. **Fire now** still works.
   Resume and the waiting mail fires, while the missed heartbeats do *not* pile up.
7. **Pause survives a restart.** Pause, Ctrl-C, restart `ui.py` — still paused.
8. **Shut down.** The header button stops the process (the page goes dead, by design).
   `pkill -f ui.py` if one ever escapes.
9. **Logic self-check** (no server): `python3 test_aoa.py` — path confinement, the
   inbox write-ban, interval and daily arithmetic, consume-once.

## Phase 2 — live on Gemma

    python3 -c "import aoa; print(aoa.BASE_URL, aoa.MODEL)"   # confirm .env loaded
    AOA_DEBUG=1 python3 ui.py                                  # prints finish_reason, tool calls, tokens

1. **Smallest possible live run.** One-shot box: `Append one line to today's log saying you woke
   up, then stop.` Debug should read `calls=['append_file']` then `finish=stop`. Check the log
   line **starts with HH:MM** — if it doesn't, the format example in the system prompt is losing.
2. **It edits its own memory.** One-shot: `Remember that invoices from Acme always go to
   accounts@, and record it as a standing instruction.` The MEMORY.md pane should change.
3. **Memory actually persists** — the real test, since every run is a fresh context: restart
   `ui.py`, then ask about Acme in a one-shot. It can only answer from MEMORY.md.
4. **Skill routing.** Drop an inbox event that is clearly noise ("newsletter: 10 AI tips") — with
   `skills/triage-inbox.md` present it should log one line and *not* write to outbox. Then drop a
   real request and watch `outbox/` fill. (Verified: an overdue-invoice event produced
   `outbox/<ts>-reply.md` in bullet style, because MEMORY.md says the owner wants bullets.)
5. **Doing nothing is correct.** Let `heartbeat` fire with nothing pending. A run that only logs
   "no actions required" is a pass. Invented work is the failure.

## Phase 3 — small-model failure modes (the ones that actually bite)

A 4-bit 12B model fails differently from a frontier model. Watch for these, all seen live:

| Symptom | What it means | Lever |
|---|---|---|
| `finish=length` in debug | hit the 2048-token cap mid-answer; the loop returns truncated text | raise `max_tokens` in `chat()`, or tell it to be terser |
| Same tool called twice in a row (`list_dir`, `list_dir`) | it forgot it already looked | harmless but it costs turns; lower `temperature`, or drop `max_turns` |
| Log line in the wrong format | **it copies whatever the log tail looks like** | fix the *existing* lines, not just the prompt — the tail is the template |
| Agent writes into `inbox/` | it can manufacture its own triggers → runaway loop | already blocked; `python3 test_aoa.py` guards it. Try `Write inbox/x.txt` and confirm the refusal |
| Prose like "I will now call append_file" with no tool call | server-side tool parsing is off | vLLM needs `--enable-auto-tool-choice --tool-call-parser <parser>` |
| Tool args arrive as invalid JSON | small-model artifact | already handled: the loop returns `ERROR ...` as the tool result and lets it retry |

There is **no advisor** on this stack — one endpoint, one model. Nothing to test for it.

## Phase 4 — failure modes worth proving

| Test | How | Expected |
|---|---|---|
| Path escape | file viewer: `../../../etc/passwd` | `ValueError: path escapes workspace` |
| Shell off | one-shot: `run ls /` | tool returns "shell is disabled" |
| Shell on | `AOA_ALLOW_SHELL=1 python3 ui.py`, same prompt | runs, cwd is `workspace/` |
| Tool error recovery | one-shot: `read nonexistent.md then carry on` | `is_error` result, agent adapts, no crash |
| Runaway loop | one-shot a task needing >12 tool calls | returns `hit max_turns`, loop survives |
| Crash mid-run | drop an event, Ctrl-C during the run | event stays in `processed/` — dropped, not replayed. Deliberate: at-most-once beats an infinite retry on a poisoned event |
| Bad trigger file | put `{"name":"x"}` in triggers.json | `ValueError: trigger x needs 'every' or 'at'` |
| Endpoint down | stop vLLM, fire a trigger | retries twice with backoff, then the run records `RuntimeError: ...` and the loop keeps ticking |
| Bad API key | break the key in `.env`, restart | `HTTP 401: ...` on the first call, no retry (4xx is not a blip) |

## Before you leave it running

`heartbeat` now ships at `every: 900` (15 min); at `every: 60` it is **1,440 runs a day**. Self-hosted, so it costs GPU time rather than
dollars — but each run is ~800-1,300 prompt tokens and 4-5 round trips, and it shares that box with
whatever else uses `llm-summary`. Raise it to `900` (15 min) once you have seen it work.

Watch `in=`/`out=` under `AOA_DEBUG=1` for a few runs to learn your real per-tick load, and watch
`created_cache_tokens` in vLLM's response if you want to see its prefix cache helping.
