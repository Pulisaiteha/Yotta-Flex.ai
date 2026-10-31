"""One place to configure logging for the CLI and the API.

Every line shows WHICH module/function/line wrote it and WHICH request it belongs to:

2026-10-31 10:00:01 | INFO  | req=a1b2c3 | src.pipeline.runner.run_pipeline:75 | step 2/7 ...

Logs go to the console and to logs/pipeline.log.
"""
import logging
import os
from contextvars import ContextVar
from logging.handlers import RotatingFileHandler
from pathlib import Path


# Set by the API middleware for each HTTP request; "-" for CLI runs.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

LOG_FORMAT = ("%(asctime)s | %(levelname)-5s | req=%(request_id)s | "
              "%(name)s.%(funcName)s:%(lineno)d | %(message)s")


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = request_id_var.get()
        return True


def setup_logging(level=None, log_file="logs/pipeline.log"):
    level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
    root = logging.getLogger()
    root.setLevel(level)
    # Avoid adding handlers twice (e.g. uvicorn --reload re-imports the app).
    if any(getattr(h, "_pipeline_handler", False) for h in root.handlers):
        return

    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(LOG_FORMAT)
    file_handler = RotatingFileHandler(log_file, maxBytes=5_000_000, backupCount=3,
                                       encoding="utf-8")
    console_handler = logging.StreamHandler()
    for handler in (file_handler, console_handler):
        handler._pipeline_handler = True
        handler.setFormatter(formatter)
        handler.addFilter(RequestIdFilter())
        root.addHandler(handler)
