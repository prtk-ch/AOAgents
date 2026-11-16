"""One runnable check for the logic that can actually break."""
import json, time
from datetime import datetime
import aoa


def main():
    # path confinement (inbox content is untrusted)
    for bad in ("../../etc/passwd", "/etc/passwd", "log/../../x"):
        try:
            aoa._safe(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"escape allowed: {bad}")
    assert aoa._safe("log/x.md").is_relative_to(aoa.WS)

    # the inbox is input-only: writing there would let the agent trigger itself
    for bad in ("inbox/x.txt", "inbox/processed/y.txt"):
        try:
            aoa._safe(bad, write=True)
        except ValueError:
            pass
        else:
            raise AssertionError(f"agent could write its own trigger: {bad}")
    assert aoa._safe("inbox/x.txt")            # reading the inbox is still fine
    assert aoa._safe("outbox/x.md", write=True)

    # interval triggers
    now = time.time()
    assert aoa.due({"name": "t", "every": 60}, now - 61, now)
    assert not aoa.due({"name": "t", "every": 60}, now - 59, now)

    # daily trigger: fires once past HH:MM, not again the same day
    hhmm = f"{datetime.now():%H:%M}"
    t = {"name": "d", "at": hhmm}
    fire_ts = datetime.now().replace(second=0, microsecond=0).timestamp()
    assert aoa.due(t, fire_ts - 10, now)
    assert not aoa.due(t, fire_ts + 1, now)

    # pause: no firings, mail kept, clock advanced so resume has no backlog
    (aoa.WS / "inbox").mkdir(parents=True, exist_ok=True)
    ev = aoa.WS / "inbox" / "pause-test.txt"
    ev.write_text("hi")
    st, future = {}, now + 10_000
    assert aoa.tick(st, future, is_paused=True) == []
    assert ev.exists(), "event consumed while paused"
    assert st["heartbeat"] == future, "clock must advance while paused"
    resumed = aoa.tick(st, future + 1, is_paused=False)
    assert any(n.startswith("event:") for n, _ in resumed), "waiting mail did not fire on resume"
    assert not any(n == "heartbeat" for n, _ in resumed), "backlog fired on resume"
    (aoa.WS / "inbox" / "processed" / "pause-test.txt").unlink()

    # events are consumed exactly once
    (aoa.WS / "inbox").mkdir(parents=True, exist_ok=True)
    p = aoa.WS / "inbox" / "test-event.txt"
    p.write_text("hello")
    state = {}
    fired = aoa.tick(state, now + 10_000)
    assert any(n == "event:test-event.txt" for n, _ in fired), fired
    assert not any(n.startswith("event:") for n, _ in aoa.tick(state, now + 10_001))
    (aoa.WS / "inbox" / "processed" / "test-event.txt").unlink()

    print("all checks pass")


if __name__ == "__main__":
    main()
