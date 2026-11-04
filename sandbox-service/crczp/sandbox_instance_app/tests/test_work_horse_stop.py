"""Tests of the graceful stop of the RQ work horse running a sandbox job."""
# pylint: disable=protected-access

import contextlib
import os
import select
import signal
import subprocess  # noqa: S404  # the tests start their own Python processes
import sys
import time
from collections.abc import Iterator
from unittest.mock import MagicMock, call, patch

import fakeredis
import pytest

from crczp.sandbox_instance_app.lib import work_horse_stop
from crczp.sandbox_instance_app.lib.work_horse_stop import (
    HORSE_KILL_DELAY_SECONDS,
    STOP_SIGNAL,
    STOPPED_EXIT_STATUS,
    GracefulStopWorker,
    interruptible_process,
)

PROCESS_PID = 4321
HORSE_PID = 4242
PROCESS_WAIT_SECONDS = 10
GRACEFUL_PROCESS_SCRIPT = """
import signal, sys, time
def stop(_signum, _frame):
    print('stopping', flush=True)
    sys.exit(3)
signal.signal(signal.SIGTERM, stop)
print('ready', flush=True)
time.sleep(30)
"""


class HorseExited(Exception):
    """Stands for the exit of the work horse, so that a test can observe it."""


@pytest.fixture(autouse=True)
def stop_state() -> Iterator[None]:
    """Start every test with no stop requested and the stop signal handled by default."""
    previous_handler = signal.getsignal(STOP_SIGNAL)
    work_horse_stop._InterruptibleProcess.stop_requested = False
    yield
    work_horse_stop._InterruptibleProcess.stop_requested = False
    work_horse_stop._InterruptibleProcess.pid = None
    work_horse_stop._InterruptibleProcess.running_block = False
    signal.signal(STOP_SIGNAL, previous_handler)


@pytest.fixture(name='exit_horse')
def exit_horse_fixture() -> Iterator[MagicMock]:
    """Replace the exit of the work horse by raising HorseExited."""
    with patch.object(work_horse_stop.os, '_exit', side_effect=HorseExited) as exit_mock:
        yield exit_mock


@pytest.fixture(name='kill')
def kill_fixture() -> Iterator[MagicMock]:
    """Record the signals sent to single processes instead of sending them."""
    with patch.object(work_horse_stop.os, 'kill') as kill_mock:
        yield kill_mock


def stop_work_horse() -> None:
    """Deliver the stop to the handler the work horse installs."""
    work_horse_stop._stop_work_horse(STOP_SIGNAL, None)


@pytest.mark.usefixtures('exit_horse')
class TestStopOfTheWorkHorse:
    """The work horse's handling of its stop."""

    def test_a_stop_while_the_process_runs_interrupts_it_and_exits_after_its_end(
        self, exit_horse, kill
    ):
        """The block goes on after the process is interrupted; the horse exits after the end."""
        steps: list[str] = []

        with (
            pytest.raises(HorseExited),
            interruptible_process(PROCESS_PID, lambda: steps.append('end')),
        ):
            stop_work_horse()
            steps.append('block went on')

        kill.assert_called_once_with(PROCESS_PID, STOP_SIGNAL)
        assert steps == ['block went on', 'end']
        exit_horse.assert_called_once_with(STOPPED_EXIT_STATUS)

    def test_a_stop_outside_any_process_exits_at_once(self, kill):
        """Without a process to let stop, the horse exits within the handler."""
        with pytest.raises(HorseExited):
            stop_work_horse()

        kill.assert_not_called()

    def test_a_stop_while_the_process_is_ended_exits_once_the_end_returns(self, kill):
        """A process already being ended gets no second signal, and its end is completed."""
        steps: list[str] = []

        def end() -> None:
            stop_work_horse()
            steps.append('end')

        with pytest.raises(HorseExited), interruptible_process(PROCESS_PID, end):
            pass

        kill.assert_not_called()
        assert steps == ['end']

    def test_a_block_left_without_a_stop_ends_the_process_and_keeps_the_horse(self, exit_horse):
        """A block cut short by an error ends its process and lets the error through."""
        ended: list[bool] = []

        with (
            pytest.raises(RuntimeError),
            interruptible_process(PROCESS_PID, lambda: ended.append(True)),
        ):
            raise RuntimeError('cut short')

        assert ended == [True]
        exit_horse.assert_not_called()

    @pytest.mark.usefixtures('kill')
    def test_the_stop_signal_is_ignored_once_the_stop_is_handled(self):
        """A repeated stop cannot interrupt the process a second time."""
        with pytest.raises(HorseExited), interruptible_process(PROCESS_PID, lambda: None):
            stop_work_horse()

        assert signal.getsignal(STOP_SIGNAL) is signal.SIG_IGN


class TestGracefulStopWorker:
    """The worker's stop of its work horse."""

    @pytest.fixture(name='worker')
    def worker_fixture(self) -> GracefulStopWorker:
        """A worker over an in-memory Redis."""
        return GracefulStopWorker(['default'], connection=fakeredis.FakeRedis())

    @pytest.fixture(name='timer')
    def timer_fixture(self) -> Iterator[MagicMock]:
        """Record the delayed kills instead of scheduling them."""
        with patch.object(work_horse_stop.threading, 'Timer') as timer_class:
            yield timer_class

    def test_kill_horse_stops_the_horse_and_kills_its_group_after_the_delay(
        self, worker, kill, timer
    ):
        """The horse gets the stop signal, and its process group the kill later."""
        worker._horse_pid = HORSE_PID

        worker.kill_horse()

        kill.assert_called_once_with(HORSE_PID, STOP_SIGNAL)
        timer.assert_called_once_with(
            HORSE_KILL_DELAY_SECONDS,
            work_horse_stop._kill_process_group,
            args=(HORSE_PID, signal.SIGKILL),
        )
        assert timer.return_value.daemon is True
        timer.return_value.start.assert_called_once_with()

    def test_kill_horse_without_a_horse_signals_nothing(self, worker, kill, timer):
        """An idle worker signals no process, its own group included."""
        worker._horse_pid = 0

        worker.kill_horse()

        kill.assert_not_called()
        timer.assert_not_called()

    def test_kill_horse_of_a_horse_gone_meanwhile_kills_no_group(self, worker, kill, timer):
        """A horse that has exited leaves no group to kill."""
        worker._horse_pid = HORSE_PID
        kill.side_effect = ProcessLookupError

        worker.kill_horse()

        timer.assert_not_called()

    def test_the_horse_handles_the_stop_signal(self, worker):
        """The work horse handles the stop signal instead of dying by it."""
        worker.setup_work_horse_signals()

        assert signal.getsignal(STOP_SIGNAL) is work_horse_stop._stop_work_horse

    def test_the_delayed_kill_reaches_the_whole_group_and_ignores_a_gone_one(self):
        """The delayed kill signals the process group the horse leads."""
        with patch.object(
            work_horse_stop.os, 'killpg', side_effect=[None, ProcessLookupError]
        ) as kill_group:
            work_horse_stop._kill_process_group(HORSE_PID, signal.SIGKILL)
            work_horse_stop._kill_process_group(HORSE_PID, signal.SIGKILL)

        assert kill_group.call_args_list == [call(HORSE_PID, signal.SIGKILL)] * 2


def read_report(report_fd: int, deadline: float) -> str:
    """Read what the horse reports until it closes the pipe or the deadline passes."""
    chunks = []
    while time.monotonic() < deadline:
        readable, _, _ = select.select([report_fd], [], [], deadline - time.monotonic())
        if not readable:
            break
        chunk = os.read(report_fd, 1024)
        if not chunk:
            break
        chunks.append(chunk.decode())
    return ''.join(chunks)


def run_horse(report_fd: int) -> None:
    """Run a process inside interruptible_process, reporting its output and exit status."""
    signal.signal(STOP_SIGNAL, work_horse_stop._stop_work_horse)
    with subprocess.Popen(  # noqa: S603
        [sys.executable, '-c', GRACEFUL_PROCESS_SCRIPT], stdout=subprocess.PIPE, text=True
    ) as process:
        assert process.stdout is not None

        def report_exit() -> None:
            os.write(report_fd, f'exit {process.wait()}\n'.encode())

        with interruptible_process(process.pid, report_exit):
            for line in process.stdout:
                os.write(report_fd, line.encode())


def wait_for_exit(pid: int, deadline: float) -> int | None:
    """Reap the process and return its wait status, or None when it outlives the deadline."""
    while time.monotonic() < deadline:
        exited_pid, status = os.waitpid(pid, os.WNOHANG)
        if exited_pid:
            return status
        time.sleep(0.05)
    return None


def test_a_stopped_horse_lets_its_process_stop_gracefully_then_exits():
    """A real horse, stopped while it reads its process, exits only after the process stopped."""
    report_read, report_write = os.pipe()
    horse_pid = os.fork()
    if horse_pid == 0:
        try:
            os.close(report_read)
            run_horse(report_write)
        finally:
            os._exit(0)
    os.close(report_write)
    deadline = time.monotonic() + PROCESS_WAIT_SECONDS
    status = None
    try:
        readable, _, _ = select.select([report_read], [], [], PROCESS_WAIT_SECONDS)
        ready = os.read(report_read, 1024).decode() if readable else ''
        assert ready == 'ready\n'

        os.kill(horse_pid, STOP_SIGNAL)
        report = ready + read_report(report_read, deadline)
        status = wait_for_exit(horse_pid, deadline)
    finally:
        os.close(report_read)
        if status is None:
            with contextlib.suppress(ProcessLookupError, ChildProcessError):
                os.kill(horse_pid, signal.SIGKILL)
                os.waitpid(horse_pid, 0)

    assert report == 'ready\nstopping\nexit 3\n'
    assert status is not None
    assert os.waitstatus_to_exitcode(status) == STOPPED_EXIT_STATUS
