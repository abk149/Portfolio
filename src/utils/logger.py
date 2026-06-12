import logging
from rich.logging import RichHandler
from config import settings


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(settings.log_level)
    handler = RichHandler(rich_tracebacks=True, show_path=False, show_time=False)
    handler.setFormatter(logging.Formatter("%(name)s | %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False
    return logger
