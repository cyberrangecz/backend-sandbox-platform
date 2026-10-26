"""
Module containing CyberRangeCZ Platform Terraform exception handlers.
"""

import re
from typing import Any, NoReturn

import structlog

LOG = structlog.get_logger()

STATE_LOCK_ID_PATTERN = re.compile(r'Lock Info:\s+ID:\s+(\S+)')


def command_error_handler(
    exception_class: type[BaseException], exception_message: str, *args: Any, **kwargs: Any
) -> NoReturn:
    """
    Handles exceptions raised by the Terraform CLI.
    :param exception_class: The exception class raised.
    :param exception_message: The exception message.
    :param args: The arguments passed to the exception.
    :param kwargs: The keyword arguments passed to the exception.
    :return:
    """
    LOG.error(exception_message, *args, **kwargs)
    raise exception_class(exception_message)


def find_state_lock_id(command_output: str) -> str | None:
    """
    Find the lock id in the output of a command that failed on a held state lock.
    :param command_output: Combined output of the failed Terraform command.
    :return: The lock id, or None if the output reports no state lock.
    """
    match = STATE_LOCK_ID_PATTERN.search(command_output)
    return match.group(1) if match else None
