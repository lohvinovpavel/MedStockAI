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


def _assess_calls() -> list[ast.Call]:
    tree = ast.parse(_MAIN.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "assess"
    ]


def test_the_source_is_where_we_think_it_is():
    assert _MAIN.is_file(), f"missing {_MAIN}"


def test_there_is_something_to_check():
    # A rename or refactor that leaves zero call sites would make every
    # assertion below vacuously true.
    assert _assess_calls(), "no assess() call sites found -- has it been renamed?"


def test_every_assess_call_passes_risk_profiles():
    missing = [
        call.lineno
        for call in _assess_calls()
        if not any(kw.arg == "risk_profiles" for kw in call.keywords)
    ]
    assert not missing, (
        f"assess() called without risk_profiles at {_MAIN.name} line(s) "
        f"{', '.join(str(n) for n in missing)}. The call will succeed and the "
        "response will look normal, but PP-3 prognosis findings will be missing "
        "from it. Pass risk_profiles=approved_profiles([...])."
    )
