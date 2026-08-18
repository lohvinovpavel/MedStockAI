"""The migrate Job has to reach a terminal state before CI stops watching.

Two deploys failed with `error: timed out waiting for the condition on
jobs/migrate` and nothing else. That message was the whole problem: it is what
`kubectl wait --for=condition=complete` prints for a job that failed, a job that
is still running, and a pod that never started, so it distinguishes none of them
-- and by the time anyone reads it the runner is gone.

Three things had to line up for the message to mean something:

* the Job needs `activeDeadlineSeconds`, or a stuck attempt never becomes either
  Complete or Failed;
* that deadline has to be **shorter** than CI's wait, or CI gives up first and
  we are back to the ambiguous message;
* the Job needs resource requests, or it is BestEffort on a spot node pool --
  scheduled last, evicted first, and indistinguishable from a hang.

These are three files that have to agree and no runtime that checks them, which
is what this pins.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]
_JOB = _ROOT / "deploy" / "k8s" / "migrate-job.yaml"
_WORKFLOW = _ROOT / ".github" / "workflows" / "deploy-dev.yml"


def _job() -> dict:
    return yaml.safe_load(_JOB.read_text(encoding="utf-8"))


def _migrate_step() -> str:
    workflow = yaml.safe_load(_WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["deploy"]["steps"]
    return next(s for s in steps if s.get("name") == "Migrate")["run"]


def test_the_job_has_a_deadline():
    """Without one, a stuck attempt has no terminal state to wait for."""
    assert _job()["spec"].get("activeDeadlineSeconds"), (
        "migrate-job needs activeDeadlineSeconds or a hung attempt never fails"
    )


def test_the_deadline_is_shorter_than_ci_waits():
    """The ordering *is* the fix. If CI gives up first it reports a timeout that
    means nothing, which is exactly the failure this came from."""
    deadline = int(_job()["spec"]["activeDeadlineSeconds"])
    waits = [int(t) for t in re.findall(r"--timeout=(\d+)s", _migrate_step())]
    assert waits, "the Migrate step should wait on the job with an explicit timeout"
    assert deadline < min(waits), (
        f"job deadline {deadline}s must be under CI's shortest wait {min(waits)}s, "
        "so the job reaches Complete or Failed before CI stops watching"
    )


def test_ci_waits_on_failure_too():
    """Waiting only on `complete` is what made a failed job look like a hang."""
    step = _migrate_step()
    assert "condition=failed" in step, (
        "the Migrate step must race condition=failed alongside condition=complete, "
        "or a failed job is indistinguishable from a slow one"
    )


def test_ci_prints_the_logs_when_it_goes_wrong():
    """The alembic traceback lives in the pod and the runner is ephemeral. If CI
    does not pull it out on failure, nobody ever sees it."""
    assert "logs job/migrate" in _migrate_step()


def test_the_job_is_not_besteffort():
    """A pod with no requests is scheduled last and evicted first. On the spot
    e2-medium pool in infra/terraform/dev/gke.tf that means Pending, which
    produces no logs and reads exactly like a hung migration."""
    container = _job()["spec"]["template"]["spec"]["containers"][0]
    requests = container.get("resources", {}).get("requests", {})
    assert requests.get("cpu") and requests.get("memory"), (
        "migrate-job needs cpu and memory requests so it schedules deterministically"
    )


def test_retries_tolerate_one_preemption_without_masking_a_real_failure():
    """Spot nodes are preempted as a matter of course, so one attempt is too
    few. Many attempts would be worse than useless: a migration that genuinely
    fails fails the same way every time, and each retry retakes the lock."""
    assert _job()["spec"]["backoffLimit"] == 2
