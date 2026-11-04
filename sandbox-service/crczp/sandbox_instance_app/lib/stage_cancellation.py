"""Cancellation of sandbox stages whose end is confirmed through the liveness of their jobs."""

import time
from collections.abc import Sequence

from crczp.sandbox_instance_app.lib.job_liveness import JobLiveness, get_jobs_liveness
from crczp.sandbox_instance_app.lib.stage_handlers import CancellableJob

STOP_CONFIRMATION_SECONDS = 20.0
LIVENESS_POLL_SECONDS = 0.5


def cancel_stages(
    handlers: Sequence[CancellableJob], wait_seconds: float = STOP_CONFIRMATION_SECONDS
) -> list[CancellableJob]:
    """
    Cancel the unfinished jobs of the handlers and return those whose stop stays pending.

    A handler is a stage or the NetBird provisioning job of an allocation request.

    Every job is stopped first, then the jobs of all stages are polled together until each
    reads dead or the wait runs out. A stage is marked failed only once its job reads dead.
    A job still queued is removed again, and one that started after its removal gets the
    stop command. A job still to be queued is stopped once its ID is stored. A stage whose
    job outlives the wait stays unfinished, stop pending.

    :param handlers: Handlers of the stages to cancel; finished stages are skipped.
    :param wait_seconds: How long to wait for the jobs to end, in seconds.
    :returns: Handlers of the stages still stop pending, in the order given.
    """
    running = [handler for handler in handlers if not handler.finished]
    for handler in running:
        handler.request_stop()
    deadline = time.monotonic() + wait_seconds
    while True:
        running = _settle(running)
        if not running or time.monotonic() >= deadline:
            return running
        time.sleep(LIVENESS_POLL_SECONDS)


def _settle(handlers: list[CancellableJob]) -> list[CancellableJob]:
    """
    Mark the stages with a dead job cancelled and return the others, stopping them again.

    A stage without a job ID is reloaded first; one whose job is still to be queued is kept
    as it is, to be stopped once its job ID is stored.
    """
    if not handlers:
        return []
    for handler in handlers:
        if handler.job_id is None:
            handler.refresh()
    running = []
    liveness_per_job = get_jobs_liveness([handler.job_id for handler in handlers])
    for handler, liveness in zip(handlers, liveness_per_job, strict=True):
        if handler.job_id is None and handler.enqueue_pending:
            running.append(handler)
            continue
        if liveness is JobLiveness.DEAD:
            handler.mark_cancelled()
            continue
        if liveness is JobLiveness.QUEUED or not handler.stop_sent:
            handler.request_stop()
        running.append(handler)
    return running
