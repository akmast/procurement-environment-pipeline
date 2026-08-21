"""
Shared logging setup for every ingestion module — writes to both the
console and a log file under `logs/` at the project root, regardless of
how a module is run (`python -m ...`, import + `run(...)`, Jupyter
Notebook, or a directly-executed script).

    from common.logging_config import setup_logging
    setup_logging()
    logger = logging.getLogger(__name__)
"""
import logging
from pathlib import Path

# common/logging_config.py -> project root is one level up
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOGS_DIR = PROJECT_ROOT / "logs"
LOG_PATH = LOGS_DIR / "pipeline.log"


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configure the root logger with a file handler and a console handler.
    Safe to call more than once (e.g. re-imported in Jupyter) — clears
    any handlers it previously added instead of stacking duplicates, so
    log lines never get printed/written twice.
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s | %(message)s", "%Y-%m-%d %H:%M:%S"
    )

    file_handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)
