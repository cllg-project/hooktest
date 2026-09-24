import os.path
import subprocess
import sys


def get_path(xml: str) -> str:
    return os.path.relpath(os.path.join(os.path.dirname(__file__), "test_data", xml))


def test_repeated_milestone_parent_does_not_crash():
    """ A milestone parent the text comes back to (<cb n="1"/> three times, like a PG column
    revisited out of order) gives children that only exist under a later occurrence: dapytains
    cannot resolve them and used to hand None to Saxon, which segfaulted. Run in a subprocess so
    that a crash fails the test instead of killing the whole test session. """
    process = subprocess.run(
        [sys.executable, "-m", "hooktest.cli", "--no-catalog", "-v", "verbose",
         get_path("milestone_repeated_parent.xml")],
        capture_output=True, text=True, timeout=300
    )
    assert process.returncode in (0, 1), f"hooktest crashed (return code {process.returncode}):\n{process.stderr}"
    output = process.stdout
    assert "duplicateRefs[Tree=default]" in output, "The duplicate check ran and reports the tree"
    assert "`1`" in output, "Column 1 is repeated"
    assert "`1.B`" in output, "Letter B is repeated under the later occurrences of column 1"
    assert "unresolvable" in output, "The unresolvable reference is reported, not crashed on (the table wraps the message)"
    assert "`2`" not in output and "`3`" not in output, "Columns 2 and 3 are not duplicated"
