"""Compile every application template, including pages with no fixture rows."""
from pathlib import Path

from django.conf import settings
from django.template.loader import get_template


def test_all_project_templates_compile_with_registered_libraries():
    root = Path(settings.BASE_DIR) / "templates"
    templates = sorted(root.rglob("*.html"))
    assert len(templates) >= 100
    for filename in templates:
        get_template(filename.relative_to(root).as_posix())
