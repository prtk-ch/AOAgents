"""One runnable check: rules fire, suppressions work, severity sets the exit code."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import slopguard


def main():
    tmp = Path(__file__).parent / ".test_tmp"
    tmp.mkdir(exist_ok=True)
    rules = tmp / "r.rules"
    rules.write_text(
        "deny | *.py | except Exception:\\s*pass | swallowed\n"
        "warn | *.py | print\\( | debug print\n"
        "deny | */ui/*.py | from app.db | layer violation\n"
        "deny | *.py | \\beval\\(|\\bexec\\( | alternation in the regex field\n"
    )
    loaded = slopguard.load_rules(rules)
    assert len(loaded) == 4
    assert loaded[3][2].search("eval(x)") and loaded[3][3] == "alternation in the regex field"

    bad = tmp / "bad.py"
    bad.write_text(
        "try:\n    x()\nexcept Exception: pass\n"      # deny
        "print('hi')\n"                                  # warn
        "except Exception: pass  # slopguard: ok rethrown upstream\n"  # suppressed
    )
    f = slopguard.check(loaded, [bad])
    sev = sorted(s for s, *_ in f)
    assert sev == ["deny", "warn"], f  # the suppressed line must not appear

    # negation: a rule scoped away from tests must not fire inside them
    notest = slopguard.load_rules(rules)[0]
    assert slopguard.matches(Path("app/x.py"), "*.py,!test_*.py")
    assert not slopguard.matches(Path("test_x.py"), "*.py,!test_*.py")
    assert not slopguard.matches(Path("a/b/test_x.py"), "*.py,!test_*.py")
    assert notest  # rule 0 is still loadable after the glob change

    # glob matches a nested path, not just the basename
    ui = tmp / "ui"; ui.mkdir(exist_ok=True)
    (ui / "page.py").write_text("from app.db import q\n")
    assert slopguard.matches(Path("app/ui/page.py"), "*/ui/*.py")
    assert not slopguard.matches(Path("app/services/page.py"), "*/ui/*.py")

    # exit code: deny -> 1, warn only -> 0
    only_warn = tmp / "warn.py"; only_warn.write_text("print('x')\n")
    assert slopguard.main(["--rules", str(rules), str(only_warn)]) == 0
    assert slopguard.main(["--rules", str(rules), str(bad)]) == 1

    for p in sorted(tmp.rglob("*"), reverse=True):
        p.unlink() if p.is_file() else p.rmdir()
    tmp.rmdir()
    print("all checks pass")


if __name__ == "__main__":
    main()
