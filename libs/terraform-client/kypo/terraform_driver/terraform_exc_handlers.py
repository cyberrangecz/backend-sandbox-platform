import structlog

LOG = structlog.get_logger()


def command_error_handler(exception_class, exception_message, *args, **kwargs):
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
