"""Opt-in Sentry tracing with privacy-safe attributes."""

import os
from contextlib import contextmanager
from typing import Any, Iterator

_sentry = None


def configure_sentry(enabled: bool | None = None) -> bool:
    global _sentry
    requested = enabled if enabled is not None else os.getenv("CRYPTGIT_SENTRY_ENABLED") == "1"
    dsn = os.getenv("SENTRY_DSN")
    if not requested or not dsn:
        _sentry = None
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.logging import ignore_logger
    except ImportError:
        _sentry = None
        return False
    sentry_sdk.init(
        dsn=dsn,
        traces_sample_rate=float(os.getenv("SENTRY_TRACES_SAMPLE_RATE", "1.0")),
        send_default_pii=False,
    )
    ignore_logger("cryptgit_studio")
    _sentry = sentry_sdk
    return True


@contextmanager
def stage(operation: str, attributes: dict[str, Any] | None = None) -> Iterator[None]:
    if _sentry is None:
        yield
        return
    with _sentry.start_span(op=operation, name=operation) as span:
        for key, value in (attributes or {}).items():
            if isinstance(value, (str, int, float, bool)):
                span.set_data(key, value)
        try:
            yield
        except Exception as error:
            span.set_data("error_type", type(error).__name__)
            span.set_status("internal_error")
            raise


def flush(timeout: float = 2.0) -> None:
    if _sentry is not None:
        _sentry.flush(timeout=timeout)
