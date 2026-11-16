# Skill: triage-inbox

Use when the trigger is `event:*`.

1. Classify the event: question / task / noise.
2. Noise -> one log line, stop.
3. Question -> answer in `outbox/<timestamp>-reply.md`.
4. Task -> if it is one step, do it. If it is multi-step or irreversible,
   consult the advisor first, then write the plan to `outbox/` and wait for the next trigger.
5. Always finish by appending one line to today's log.
