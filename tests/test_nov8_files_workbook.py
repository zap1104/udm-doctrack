"""Spreadsheet dimension caches must never decide readable coverage."""

import io
import re
import zipfile
from dataclasses import replace

import pytest
from openpyxl import Workbook

from apps.documents.extraction import extract_text_layer
from apps.documents.processing_contracts import ExtractionPolicy
from apps.documents.source_readers import read_source_units


def workbook_with_dimension(dimension):
    workbook = Workbook()
    workbook.active["A1"] = "Opening cell"
    workbook.active["C3"] = "Late actual cell"
    workbook.active["L8"] = "Final appendix evidence"
    original = io.BytesIO()
    workbook.save(original)
    changed = io.BytesIO()
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(changed, "w") as target:
        for name in source.namelist():
            data = source.read(name)
            if name == "xl/worksheets/sheet1.xml":
                data = re.sub(rb'<dimension[^>]*/>', dimension, data)
            target.writestr(name, data)
    return changed.getvalue()


@pytest.mark.parametrize("dimension", [
    b'<dimension ref="A1:A1"/>', b"", b'<dimension ref="A1:XFD1048576"/>',
], ids=["understated", "missing", "overstated"])
def test_actual_worksheet_cells_determine_complete_extraction(dimension):
    payload = workbook_with_dimension(dimension)
    batch = read_source_units(io.BytesIO(payload), "dimension-cache.xlsx", policy=ExtractionPolicy())
    text = "\n".join(unit.normalized_text for unit in batch.units)

    assert "A1: Opening cell" in text
    assert "C3: Late actual cell" in text
    assert "L8: Final appendix evidence" in text
    assert batch.coverage == "complete"
    assert not batch.omitted_reasons


def test_legacy_extraction_includes_cells_beyond_a_stale_dimension():
    payload = workbook_with_dimension(b'<dimension ref="A1:A1"/>')

    result = extract_text_layer(io.BytesIO(payload), "dimension-cache.xlsx")

    assert result.status == "DONE"
    assert "Final appendix evidence" in result.text
    assert not result.notes


@pytest.mark.parametrize("budget", ["rows", "columns", "cells"])
def test_real_unread_cells_are_disclosed_even_with_understated_cache(budget):
    payload = workbook_with_dimension(b'<dimension ref="A1:A1"/>')
    policy = replace(ExtractionPolicy(), **{
        "rows": {"max_rows": 2}, "columns": {"max_columns": 2}, "cells": {"max_cells": 12},
    }[budget])
    batch = read_source_units(io.BytesIO(payload), "bounded.xlsx", policy=policy)

    assert "Final appendix evidence" not in "\n".join(unit.normalized_text for unit in batch.units)
    assert batch.coverage != "complete"
    assert batch.expected_units is None
    assert batch.omitted_reasons


def test_large_worksheet_part_keeps_readable_cells_and_other_sheets():
    workbook = Workbook()
    workbook.active['A1'] = 'Cell in large worksheet'
    workbook.create_sheet('Other sheet')['A1'] = 'Cell in other worksheet'
    original = io.BytesIO()
    workbook.save(original)
    changed = io.BytesIO()
    with zipfile.ZipFile(original) as source, zipfile.ZipFile(changed, 'w', compression=zipfile.ZIP_DEFLATED) as target:
        for name in source.namelist():
            data = source.read(name)
            if name == 'xl/worksheets/sheet1.xml':
                padding = (b'<!--' + b'x' * 1024 * 1024 + b'-->') * 17
                data = data.replace(b'<sheetData>', b'<sheetData>' + padding, 1)
            target.writestr(name, data)

    batch = read_source_units(changed, 'large-part.xlsx', policy=ExtractionPolicy())
    text = '\n'.join(unit.normalized_text for unit in batch.units)
    assert 'A1: Cell in large worksheet' in text
    assert 'A1: Cell in other worksheet' in text
    assert batch.coverage == 'complete'
