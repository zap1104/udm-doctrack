#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def make_console_printable() -> None:
    """Stop an unprintable character from killing a command that has succeeded.

    A stock Windows console runs cp1252, which has no code point for most of
    the punctuation this project writes — arrows, tick marks — nor for anything
    an office might legitimately type into a subject line. Printing one raises
    UnicodeEncodeError, and because the output happens *after* the work, the
    command does its job and then dies reporting success, exiting non-zero. CI
    that trusts the exit code reads that as a failure.

    Switching the error handler leaves the encoding alone (so nothing turns to
    mojibake on a console that copes) and renders whatever it cannot represent
    as an escape rather than raising. UTF-8 consoles are unaffected.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:  # a stream that is not a TextIOWrapper
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (ValueError, OSError):  # pragma: no cover - already detached/closed
            pass


def main() -> None:
    make_console_printable()
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:  # pragma: no cover
        raise ImportError(
            "Django is not installed or the virtual environment is not active.\n"
            "Run:  python -m venv .venv  &&  .venv/bin/pip install -r requirements.txt\n"
            "(Windows:  py -m venv .venv  &&  .venv\\Scripts\\pip install -r requirements.txt)"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
