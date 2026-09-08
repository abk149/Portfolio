"""Scheduled jobs.

`jobs` must stay importable without APScheduler so the individual job functions
can be triggered manually on builds that don't ship it (the Android APK).
"""
from .jobs import HAS_APSCHEDULER, build_scheduler, run_blocking  # noqa: F401
