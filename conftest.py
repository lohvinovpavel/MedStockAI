"""Make a workspace of same-shaped services collectable in one pytest run.

All nine services ship a top-level package named `app` and uv installs them into
one venv, so a bare `import app` resolves to whichever was installed last. That
breaks a whole-repo `pytest` in two separate ways:

* at **collection**, the second `services/*/tests/test_health.py` imports the
  first service's `app.main`;
* at **run time**, `monkeypatch.setattr("app.main.search_concepts", …)` re-reads
  `app.main` by name, and must get the same module object the test imported.

So `app` is *swapped*, not purged. Each service's modules are stashed on the way
out and restored on the way back in, which keeps module identity stable — delete
them instead and a string-based patch silently lands on a fresh module while the
TestClient keeps using the old one.

The real fix is for services to stop sharing a package name. Until then this is
what makes `uv run pytest` mean anything.
"""

import sys
from pathlib import Path
from types import ModuleType

_stashed: dict[str, dict[str, ModuleType]] = {}
_active: str | None = None


def _service_root(path: Path) -> Path | None:
    """The `services/<name>/` directory owning this file, if any."""
    for parent in path.parents:
        if parent.parent.name == "services" and (parent / "app").is_dir():
            return parent
    return None


def _app_modules() -> dict[str, ModuleType]:
    return {k: v for k, v in sys.modules.items() if k == "app" or k.startswith("app.")}


def _activate(root: Path) -> None:
    global _active
    key = str(root)
    if key == _active:
        return

    if _active is not None:
        _stashed[_active] = _app_modules()
    for name in list(_app_modules()):
        del sys.modules[name]
    for name, module in _stashed.get(key, {}).items():
        sys.modules[name] = module

    while key in sys.path:
        sys.path.remove(key)
    sys.path.insert(0, key)
    _active = key


def _switch_for(path) -> None:
    if path is None:
        return
    root = _service_root(Path(path))
    if root is not None:
        _activate(root)


def pytest_collectstart(collector) -> None:
    path = getattr(collector, "path", None)
    if path is not None and Path(path).suffix == ".py":
        _switch_for(path)


def pytest_runtest_setup(item) -> None:
    # Collection order and execution order are not the same walk, so the active
    # service has to be re-established before each test as well.
    _switch_for(getattr(item, "path", None))
