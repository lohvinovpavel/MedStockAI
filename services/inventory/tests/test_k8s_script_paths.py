"""Every manifest that runs a script by path must resolve it inside the image.

The root Dockerfile copies `scripts/` to /srv but ends on
`WORKDIR /srv/services/<service>`. So a container that runs
`python scripts/seed_patients.py` without an explicit `workingDir` looks for the
file under the *service* directory, finds nothing, and exits "No such file or
directory" -- which is what happened to seed-patients in the cluster while the
identical seed-stock Job, which does set `workingDir`, worked fine.

Nothing here is checkable by eye: the path is in one file, the working directory
that makes it valid is in a second, and the file it has to resolve to is in a
third. This walks every Job and CronJob and confirms the three agree.

It also resolves the path against the real repo, so renaming or deleting a
script without updating the manifest that invokes it fails here rather than in
a cluster twenty minutes into a deploy.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[3]
_K8S = _ROOT / "deploy" / "k8s"

# Where the image leaves the repo. Mirrors `WORKDIR /srv` in the Dockerfile.
_IMAGE_ROOT = "/srv"


def _containers():
    """(manifest, container) for every pod template in deploy/k8s."""
    for path in sorted(_K8S.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if not doc:
                continue
            spec = doc.get("spec", {})
            # Job/Deployment carry the template directly; CronJob nests it.
            template = spec.get("template") or spec.get("jobTemplate", {}).get(
                "spec", {}
            ).get("template", {})
            for container in template.get("spec", {}).get("containers", []):
                yield path, container


def _script_invocations():
    """Containers running `python <relative/path.py>` rather than `-m module`.

    `-m` is resolved by the interpreter through the installed package, so the
    working directory does not matter. A bare path is resolved by the shell
    against the cwd, and that is the case this guards.
    """
    for path, container in _containers():
        command = container.get("command") or []
        if len(command) >= 2 and command[0] == "python" and command[1].endswith(".py"):
            yield path, container, command[1]


def test_there_is_something_to_check():
    """A refactor that moved these to `-m` would make the tests below vacuous."""
    assert list(_script_invocations()), "expected at least one script-by-path container"


@pytest.mark.parametrize(
    "manifest,container,script",
    list(_script_invocations()),
    ids=lambda v: v.name if isinstance(v, Path) else None,
)
def test_script_resolves_inside_the_image(manifest, container, script):
    working_dir = container.get("workingDir")
    assert working_dir, (
        f"{manifest.name}: runs `python {script}` with no workingDir. The image "
        f"ends on WORKDIR /srv/services/<service>, so this resolves to the wrong "
        f"directory and the container exits 'No such file or directory'."
    )

    # Where the container would actually look, then the same path in the repo.
    resolved = f"{working_dir.rstrip('/')}/{script}"
    assert resolved.startswith(f"{_IMAGE_ROOT}/"), (
        f"{manifest.name}: workingDir {working_dir} puts {script} outside {_IMAGE_ROOT}"
    )
    in_repo = _ROOT / resolved[len(_IMAGE_ROOT) + 1 :]
    assert in_repo.is_file(), (
        f"{manifest.name}: runs `python {script}` from {working_dir}, which is "
        f"{resolved} in the image, but {in_repo.relative_to(_ROOT)} does not exist "
        f"in the repo -- so it will not exist in the image either."
    )


@pytest.mark.parametrize(
    "manifest,container,script",
    list(_script_invocations()),
    ids=lambda v: v.name if isinstance(v, Path) else None,
)
def test_the_script_directory_is_copied_into_the_image(manifest, container, script):
    """A correct path and workingDir still fail if the Dockerfile never copied
    the directory -- the three have to agree, so check the third."""
    dockerfile = (_ROOT / "Dockerfile").read_text(encoding="utf-8")
    top_level = script.split("/")[0]
    assert f"COPY {top_level} ./{top_level}" in dockerfile, (
        f"{manifest.name} runs {script}, but the Dockerfile has no "
        f"`COPY {top_level} ./{top_level}` -- it will be absent from the image."
    )
