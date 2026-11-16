# slopguard — the trust pyramid, minus the parts you can install

From "All Code Is Slop (Except Mine)" (Lauren Tan / poteto, SpaceXSI): 11,000 PRs a month at a
~0.5% revert rate, because the *verification* is machine-enforced, not reviewed by hand.

**Nothing here is wired into any of your repos.** It runs standalone until you say otherwise.

## Half of it is an install, not a build

Her skills are a published Claude Code plugin. Don't reimplement them:

    claude plugin marketplace add cursor/plugins
    claude plugin install pstack@cursor-plugins
    # then, in a session:  /pstack:setup-pstack   (pick models)  then  /pstack:poteto-mode <task>

Third-party marketplace, so it runs as you and auto-update is off by default.
23 playbooks (bug-fix, refactor, hillclimb, shipping, autonomous-run), plus `interrogate`,
`unslop`, `no-comments`, `tdd`. That covers the *process* layer of the talk.

## The half nothing ships for you

The pyramid's base is your repo's own rules, and those are the ones an agent breaks:

| Talk | Mechanism | Here |
|---|---|---|
| Enforceable architecture ("Dune") | an illegal import fails the build | `deny` rules on import lines, scoped by path glob |
| Comments → lint rules | a convention agents can't ignore | `slopguard.rules`, one line per convention |
| Runtime verification | assertions + tests | whatever suites your repo has, run by `verify.sh` |
| Formal verification | TLA+ / Lean | not here — worth it only for a protocol or a state machine |
| Spend tokens on verification | agent must pass the gate before "done" | `verify.sh` as a Stop hook (below) |

    ./slopguard.py                 # check tracked files, exit 1 on any deny
    ./slopguard.py path/to/file.py # check specific files
    ./verify.sh                    # the whole gate: real tools, then the rules
    python3 test_slopguard.py      # self-check

### Rule format

    severity | path globs | forbidden regex | message

- `severity` — `deny` (exit 1) or `warn` (reported, exit 0). Start new rules at `warn`.
- `path globs` — comma-separated, leading `!` excludes: `*.py,!test_*.py,!tests/*`.
  Tests break production rules on purpose; a rule set without exclusions is one you turn off.
- `forbidden regex` — matched per line, may contain `|`.
- Justified exceptions carry a trailing `# slopguard: ok <why>` **on the offending line**.

The 20 starter rules are worth less than the ones you add. The loop that matters: an agent
makes a mistake once → that mistake becomes a line in `slopguard.rules` → it cannot recur.
That is the whole talk, compressed.

## Wiring it in (when you want it)

Per repo, copy `slopguard/` in and add to `.claude/settings.json`:

```json
{ "hooks": { "Stop": [ { "matcher": "*", "hooks": [
  { "type": "command", "command": "bash slopguard/verify.sh" } ] } ] } }
```

A non-zero exit feeds the output back to the agent instead of letting it finish, which is the
"spend tokens on verification" lever: the agent pays to fix what it broke, not you.
`PreToolUse` on `git commit` is the cheaper variant if a Stop gate feels too tight.

CI is the same one line: `bash slopguard/verify.sh`.

## Deliberately skipped

- **No TLA+/Lean.** Formal methods pay off on concurrency and protocols — `huddle`'s handoff
  state machine is the one thing you own that might deserve TLA+. Nothing else here would.
- **No AST parsing.** Line regexes have false positives, hence the `# slopguard: ok` knob.
  If a rule needs real structure, write it in `ruff`/`golangci-lint` instead — those parse.
- **No feature map / revert-rate dashboard.** That needs merge volume you don't have yet.
