"""Small authored parser fixtures, containing no institutional records."""
import io
import zlib

from PIL import Image, ImageDraw
from pypdf import PdfWriter
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, NameObject, NumberObject


def image_bytes(size=(300, 160)):
    image = Image.new("RGB", size, "white")
    ImageDraw.Draw(image).text((10, 50), "SIGNED APPENDIX CONTENT", fill="black")
    stream = io.BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


def pdf_bytes(*, scanned=True, nested=False, second_text=""):
    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=800)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"), NameObject("/BaseFont"): NameObject("/Helvetica")})
    resources = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    text = b"BT /F1 12 Tf 30 760 Td (A sufficiently long digital header above a scanned body) Tj ET\n"
    if scanned:
        from pypdf.generic import EncodedStreamObject
        image = Image.open(io.BytesIO(image_bytes())).convert("RGB")
        xobject = EncodedStreamObject()
        xobject._data = zlib.compress(image.tobytes())
        xobject.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(image.width), NameObject("/Height"): NumberObject(image.height),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8),
            NameObject("/Filter"): NameObject("/FlateDecode")})
        reference = writer._add_object(xobject)
        if nested:
            form = DecodedStreamObject()
            form.set_data(b"q 1 0 0 1 0 0 cm /I Do Q")
            form.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Form"),
                NameObject("/BBox"): ArrayObject([NumberObject(0), NumberObject(0), NumberObject(1), NumberObject(1)]),
                NameObject("/Resources"): DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/I"): reference})})})
            reference = writer._add_object(form)
        resources[NameObject("/XObject")] = DictionaryObject({NameObject("/I"): reference})
        # Identical overlapping placements must not double the covered area.
        text += b"q 500 0 0 600 50 50 cm /I Do Q\nq 500 0 0 600 50 50 cm /I Do Q\n"
    page[NameObject("/Resources")] = resources
    content = DecodedStreamObject()
    content.set_data(text)
    page[NameObject("/Contents")] = writer._add_object(content)
    if second_text:
        second = writer.add_blank_page(width=600, height=800)
        second[NameObject("/Resources")] = resources
        body = DecodedStreamObject()
        body.set_data(b"BT /F1 12 Tf 30 760 Td (" + second_text.encode("ascii") + b") Tj ET")
        second[NameObject("/Contents")] = writer._add_object(body)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()
