#!/usr/bin/env python3
"""Catches the three template mistakes that cause most runtime 500s.

    python scripts/check_templates.py

Checks:
  1. Block tags are balanced ({% if %} without {% endif %}, and so on)
  2. Every {% extends %} and {% include %} target actually exists
  3. Every {% url 'app:name' %} matches a real URL name
  4. Every {% load %} library exists

Runs without Django installed, so it is usable in CI before anything is set up.
It is a linter, not a substitute for opening the pages in a browser.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates"

PAIRED = {
    "if": "endif",
    "for": "endfor",
    "block": "endblock",
    "with": "endwith",
    "comment": "endcomment",
    "spaceless": "endspaceless",
    "autoescape": "endautoescape",
    "filter": "endfilter",
    "blocktrans": "endblocktrans",
    "verbatim": "endverbatim",
}
NEUTRAL = {"else", "elif", "empty"}

TAG_RE = re.compile(r"{%\s*(\w+)")
URL_RE = re.compile(r"""{%\s*url\s+['"]([\w:.-]+)['"]""")
INCLUDE_RE = re.compile(r"""{%\s*(?:include|extends)\s+['"]([^'"]+)['"]""")
LOAD_RE = re.compile(r"{%\s*load\s+([\w\s]+?)\s*%}")
NAME_RE = re.compile(r"""name\s*=\s*['"]([\w_-]+)['"]""")
APPNAME_RE = re.compile(r"""app_name\s*=\s*['"](\w+)['"]""")

BUILTIN_LIBRARIES = {
    "static", "i18n", "l10n", "tz", "cache", "humanize", "admin_urls", "log", "mathfilters",
}


def known_url_names() -> set[str]:
    names: set[str] = set()
    for path in ROOT.glob("apps/*/urls.py"):
        text = path.read_text(encoding="utf-8")
        namespace_match = APPNAME_RE.search(text)
        namespace = namespace_match.group(1) if namespace_match else ""
        for name in NAME_RE.findall(text):
            names.add(f"{namespace}:{name}" if namespace else name)
    # Django's own admin namespace and auth views
    names.update({"admin:index", "django-admin:index"})
    return names


def known_libraries() -> set[str]:
    libraries = set(BUILTIN_LIBRARIES)
    for path in ROOT.glob("apps/*/templatetags/*.py"):
        if path.stem != "__init__":
            libraries.add(path.stem)
    return libraries


def check_balance(path: Path, text: str, problems: list[str]) -> None:
    stack: list[tuple[str, int]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        # Ignore anything inside a {% verbatim %} block for simplicity.
        for tag in TAG_RE.findall(line):
            if tag in PAIRED:
                stack.append((tag, line_number))
            elif tag in NEUTRAL:
                continue
            elif tag.startswith("end"):
                expected = tag[3:]
                if not stack:
                    problems.append(f"{path}:{line_number}: {{% {tag} %}} with nothing open")
                elif stack[-1][0] != expected:
                    opened, opened_line = stack[-1]
                    problems.append(
                        f"{path}:{line_number}: found {{% {tag} %}} but {{% {opened} %}} "
                        f"from line {opened_line} is still open"
                    )
                    stack.pop()
                else:
                    stack.pop()
    for tag, line_number in stack:
        problems.append(f"{path}:{line_number}: {{% {tag} %}} is never closed")


def main() -> int:
    if not TEMPLATES.exists():
        print(f"No templates directory at {TEMPLATES}")
        return 1

    url_names = known_url_names()
    libraries = known_libraries()
    template_files = sorted(TEMPLATES.rglob("*.html"))
    relative = {str(path.relative_to(TEMPLATES)).replace("\\", "/") for path in template_files}

    problems: list[str] = []

    for path in template_files:
        text = path.read_text(encoding="utf-8")
        shown = path.relative_to(ROOT)

        check_balance(shown, text, problems)

        for target in INCLUDE_RE.findall(text):
            if target not in relative:
                problems.append(f"{shown}: includes '{target}', which does not exist")

        for name in URL_RE.findall(text):
            if name not in url_names:
                problems.append(f"{shown}: {{% url '{name}' %}} does not match any URL name")

        for group in LOAD_RE.findall(text):
            for library in group.split():
                if library not in libraries:
                    problems.append(f"{shown}: {{% load {library} %}} — no such template library")

    print(f"Checked {len(template_files)} template(s).")
    if problems:
        print(f"\n{len(problems)} problem(s) found:\n")
        for problem in problems:
            print(f"  FAIL  {problem}")
        return 1

    print("OK  No problems found.")
    return 0


def _make_console_printable() -> None:
    """See manage.py's version. Repeated rather than imported because this
    script deliberately runs before Django (and before manage.py) is usable.

    Without it a stock Windows cp1252 console turned the success line into an
    UnicodeEncodeError traceback, so a clean run of this linter exited 1 —
    reporting a failure it had just finished proving was not there. A template
    path or tag name can carry a non-cp1252 character too, so the failure list
    needs the same protection as the summary.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(errors="backslashreplace")
        except (ValueError, OSError):
            pass


if __name__ == "__main__":
    _make_console_printable()
    sys.exit(main())
