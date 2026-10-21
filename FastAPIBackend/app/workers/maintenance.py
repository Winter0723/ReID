"""Run one continuous maintenance service alongside API and RQ."""
import logging
import time

from app.core.observability import configure_logging
from app.services.task_recovery import recover_variants


def main():
    configure_logging()
    cursor = 0
    while True:
        try:
            cursor = recover_variants(after_id=cursor)
        except Exception:
            logging.exception("Task recovery failed; retrying next minute")
        time.sleep(60)


if __name__ == "__main__":
    main()
