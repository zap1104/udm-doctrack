#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main() -> None:
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
