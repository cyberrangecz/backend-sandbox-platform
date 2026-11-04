"""
Graceful stop of the RQ work horse running a sandbox job.

A stopped work horse interrupts the OpenTofu process it runs, so that the process records what
it built in its state and releases the state lock, and exits once that process has ended.
"""

import contextlib
import os
import signal
import sys
import threading
from collections.abc import Callable, Iterator
from types import FrameType
from typing import NoReturn, override

import structlog
from rq.worker import SHUTDOWN_SIGNAL, Worker

LOG = structlog.get_logger()
STOP_SIGNAL = signal.SIGTERM
STOPPED_EXIT_STATUS = 1
HORSE_KILL_DELAY_SECONDS = 35
"""
How long a stopped work horse may take to let its OpenTofu process stop and to exit, before
its whole process group is killed.
"""


class _InterruptibleProcess:  # pylint: disable=too-few-public-methods
    """The process the work horse runs that a stop interrupts, as the stop handler reads it."""

    pid: int | None = None
    running_block: bool = False
    stop_requested: bool = False


class GracefulStopWorker(Worker):
    """
    RQ worker that stops a work horse by STOP_SIGNAL instead of killing it, and kills the
    horse's process group only when it outlives HORSE_KILL_DELAY_SECONDS.
    """

    @override
    def setup_work_horse_signals(self) -> None:
        super().setup_work_horse_signals()
        signal.signal(STOP_SIGNAL, _stop_work_horse)

    @override
    def kill_horse(self, sig: signal.Signals = SHUTDOWN_SIGNAL) -> None:
        horse_pid = self.horse_pid
        if not horse_pid:
            return
        try:
            os.kill(horse_pid, STOP_SIGNAL)
        except ProcessLookupError:
            return
        LOG.info('Work horse stop requested', horse_pid=horse_pid)
        timer = threading.Timer(
            HORSE_KILL_DELAY_SECONDS, _kill_process_group, args=(horse_pid, sig)
        )
        timer.daemon = True
        timer.start()


@contextlib.contextmanager
def interruptible_process(pid: int, end: Callable[[], None]) -> Iterator[None]:
    """
    Run the block that waits for the process, and end on leaving it however it is left.

    A stop of the work horse while the block runs interrupts the process with STOP_SIGNAL and
    lets the block go on; the work horse exits once end has returned.

    :param pid: The process the stop interrupts.
    :param end: Ends the process if it still runs, and returns once it has exited.
    """
    _InterruptibleProcess.pid = pid
    _InterruptibleProcess.running_block = True
    try:
        yield
    finally:
        _InterruptibleProcess.pid = None
        end()
        _InterruptibleProcess.running_block = False
        if _InterruptibleProcess.stop_requested:
            _exit_stopped_work_horse()


def _stop_work_horse(_signum: int, _frame: FrameType | None) -> None:
    """
    Handle the stop of the work horse: exit at once outside interruptible_process, else
    interrupt its process, if still running, and leave the exit to the block.
    """
    signal.signal(STOP_SIGNAL, signal.SIG_IGN)
    if not _InterruptibleProcess.running_block:
        _exit_stopped_work_horse()
    _InterruptibleProcess.stop_requested = True
    pid = _InterruptibleProcess.pid
    if pid is not None:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, STOP_SIGNAL)


def _exit_stopped_work_horse() -> NoReturn:
    LOG.warning('Work horse stopped')
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(STOPPED_EXIT_STATUS)


def _kill_process_group(horse_pid: int, sig: signal.Signals) -> None:
    """Kill what is left of the process group the work horse leads."""
    try:
        os.killpg(horse_pid, sig)
    except ProcessLookupError:
        return
    LOG.warning('Killed the process group of a stopped work horse', horse_pid=horse_pid)
