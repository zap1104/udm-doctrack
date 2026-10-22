"""Conservative placed-image geometry and bounded visual PDF assessment.

The mask estimates union area of transformed placements, not image resources.
Unsupported clipping is disclosed; a blank-looking raster is never verified blank.
"""
from __future__ import annotations

import math
import threading

PDFIUM_LOCK = threading.RLock()  # PDFium is not thread-safe, even across PDFs.
IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _compose(parent, local):
    a, b, c, d, e, f = parent
    g, h, i, j, k, translation_y = map(float, local)
    result = (a*g+c*h, b*g+d*h, a*i+c*j, b*i+d*j, a*k+c*translation_y+e, b*k+d*translation_y+f)
    if not all(math.isfinite(value) and abs(value) < 1e9 for value in result):
        raise ValueError("Unsafe PDF placement transform")
    return result


def _polygon(matrix, bounds=(0, 0, 1, 1)):
    a, b, c, d, e, f = matrix
    left, bottom, right, top = map(float, bounds)
    return [(a*x+c*y+e, b*x+d*y+f) for x, y in ((left,bottom), (right,bottom), (right,top), (left,top))]


def placed_image_coverage(page):
    from PIL import Image, ImageChops, ImageDraw
    from pypdf.generic import ContentStream

    box = page.cropbox
    width, height = float(box.width), float(box.height)
    if not width > 0 or not height > 0:
        raise ValueError("Invalid PDF page dimensions")
    side = 512
    mask = Image.new("1", (side, side))
    warnings = []
    seen = [0]

    def pixel_polygon(polygon):
        return [((x-float(box.left))*side/width, (y-float(box.bottom))*side/height) for x, y in polygon]

    def add(matrix, clips):
        region = Image.new("1", (side, side))
        ImageDraw.Draw(region).polygon(pixel_polygon(_polygon(matrix)), fill=1)
        for clip in clips:
            clip_mask = Image.new("1", (side, side))
            ImageDraw.Draw(clip_mask).polygon(pixel_polygon(clip), fill=1)
            region = ImageChops.logical_and(region, clip_mask)
        mask.paste(ImageChops.logical_or(mask, region))

    def walk(stream, resources, matrix=IDENTITY, clips=(), depth=0, ancestors=()):
        if depth > 8:
            raise ValueError("PDF form nesting exceeds the assessment limit")
        stack = []
        for operands, operator in ContentStream(stream, page.pdf).operations:
            seen[0] += 1
            if seen[0] > 50_000:
                raise ValueError("PDF placement operation limit reached")
            if operator == b"q":
                if len(stack) >= 128:
                    raise ValueError("PDF graphics stack limit reached")
                stack.append(matrix)
            elif operator == b"Q":
                if not stack:
                    raise ValueError("Invalid PDF graphics stack")
                matrix = stack.pop()
            elif operator == b"cm":
                matrix = _compose(matrix, operands)
            elif operator in {b"W", b"W*"}:
                warnings.append("Complex clipping makes placed-image coverage an upper-bound estimate.")
            elif operator == b"INLINE IMAGE":
                add(matrix, clips)
            elif operator == b"Do":
                objects = resources.get("/XObject", {})
                obj = objects.get(operands[0])
                if obj is None:
                    raise ValueError("Missing PDF drawing resource")
                obj = obj.get_object()
                if obj.get("/Subtype") == "/Image":
                    add(matrix, clips)
                elif obj.get("/Subtype") == "/Form":
                    if id(obj) in ancestors:
                        raise ValueError("Cyclic PDF form")
                    transform = _compose(matrix, obj.get("/Matrix", IDENTITY))
                    form_clips = clips + ((_polygon(transform, obj["/BBox"]),) if "/BBox" in obj else ())
                    walk(obj, obj.get("/Resources", resources), transform, form_clips, depth+1, (*ancestors, id(obj)))

    contents = page.get_contents()
    if contents is not None:
        walk(contents, page.get("/Resources", {}))
    return sum(mask.convert("L").histogram()[1:]) / (side*side), tuple(dict.fromkeys(warnings))


def assess_pdf_page(page, rendered_page, *, policy):
    text = page.extract_text() or ""
    warnings = []
    try:
        coverage, geometry_warnings = placed_image_coverage(page)
        warnings.extend(geometry_warnings)
    except Exception:
        coverage = None
        warnings.append("Placed-image coverage could not be established safely.")
    useful = len(text.strip()) >= policy.native_min_chars and text.count("\ufffd") / max(len(text), 1) < 0.01
    ocr_needed = not useful or coverage is None or coverage >= policy.image_coverage_threshold or bool(warnings)
    if rendered_page is None:
        warnings.append("Visual coverage could not be assessed; the native text is still available.")
        ocr_needed = True
    else:
        gray = rendered_page.convert("L")
        histogram = gray.histogram()
        nonwhite = sum(histogram[:245]) / max(1, gray.width * gray.height)
        if nonwhite < 0.0005:
            warnings.append("The page appears near-blank; faint writing or signatures may remain unread.")
            ocr_needed = True
    if coverage and not ocr_needed:
        warnings.append("Small embedded image regions were not read as text.")
    return {"text": text, "image_coverage": coverage, "ocr_needed": ocr_needed,
            "warnings": tuple(dict.fromkeys(warnings)), "geometry": "placed-polygon-union-512-v1"}
