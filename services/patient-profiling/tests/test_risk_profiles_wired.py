"""Every assess() call in the service must pass risk_profiles.

PP-3 fails silently when it is forgotten. Extraction runs, the table fills,
the matcher works when called directly -- and the endpoint that omits the
argument returns a perfectly well-formed assessment with the prognosis stage
quietly absent. No error, no empty field, nothing in a log. The response looks
exactly like a patient for whom no profile happened to apply.

That is precisely how /assess shipped broken, and how /cart-check was written
the same way afterwards: the signature defaults risk_profiles to (), so the
call is valid Python and valid at runtime. Nothing but a reader catches it.

So this reads the source rather than the behaviour. A behavioural test would
need a database, a patient row and an approved profile, which is a lot of
scaffolding to protect against what is really a one-word omission -- and it
would only cover the endpoints someone remembered to write a test for. This
covers every call site including ones added later.
"""

from __future__ import annotations

import ast
from pathlib import Path

_MAIN = Path(__file__).resolve().parents[1] / "app" / "main.py"
# The cohort path too. plan_demand and best_substitute both called assess()
# without profiles, which made PP-4's at_risk blind to PP-3 -- the two are
# supposed to come from the same approved table precisely so they cannot
# disagree (docs/prognosis-and-procurement.md §3).
_SHARED = Path(__file__).resolve().parents[3] / "shared" / "medstock_shared" / "patient.py"

_SOURCES = (_MAIN, _SHARED)


def _assess_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "assess"
    ]


def test_the_sources_are_where_we_think_they_are():
    for path in _SOURCES:
        assert path.is_file(), f"missing {path}"


def test_there_is_something_to_check():
    # A rename or refactor that leaves zero call sites would make every
    # assertion below vacuously true.
    for path in _SOURCES:
        assert _assess_calls(path), f"no assess() call sites in {path.name} -- renamed?"


def test_every_assess_call_passes_risk_profiles():
    missing: list[str] = []
    for path in _SOURCES:
        missing += [
            f"{path.name}:{call.lineno}"
            for call in _assess_calls(path)
            if not any(kw.arg == "risk_profiles" for kw in call.keywords)
        ]
    assert not missing, (
        f"assess() called without risk_profiles at {', '.join(missing)}. The call "
        "will succeed and the result will look normal, but PP-3 prognosis findings "
        "will be missing from it -- silently, because risk_profiles defaults to ()."
    )
