"""H2 CI check: a prompt edit without bumping prompt_version fails the build."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from medstock_shared.ai_tasks import TASKS

_FINGERPRINTS = Path(__file__).resolve().parents[3] / "shared" / "medstock_shared" / "ai" / "prompt_fingerprints.json"


def _sha(prompt: str) -> str:
    return hashlib.sha256(prompt.encode()).hexdigest()


def test_prompt_change_without_version_bump_fails_ci():
    stored = json.loads(_FINGERPRINTS.read_text(encoding="utf-8"))
    current = {
        name: {"prompt_version": task.prompt_version, "sha256": _sha(task.prompt)}
        for name, task in TASKS.items()
    }
    for name, now in current.items():
        prev = stored.get(name)
        assert prev is not None, f"add {name} to prompt_fingerprints.json"
        if now["sha256"] != prev["sha256"]:
            assert now["prompt_version"] != prev["prompt_version"], (
                f"{name}: prompt changed without bumping prompt_version"
            )
        assert now == stored[name], (
            f"{name}: update prompt_fingerprints.json after bumping prompt_version"
        )
    extra = set(stored) - set(current)
    assert not extra, f"stale fingerprint entries: {sorted(extra)}"
