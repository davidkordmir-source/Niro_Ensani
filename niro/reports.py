"""Editable spreadsheet, document, and slide exports with bounded working memory."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from io import BytesIO
from itertools import islice
import os
from pathlib import Path
import re
from tempfile import mkstemp
import textwrap
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree as ET


@dataclass(frozen=True, slots=True)
class Report:
    title: str
    headings: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    language: str = "fa"
    style: str = "formal"

    @property
    def rtl(self) -> bool:
        return self.language == "fa"

    @property
    def font(self) -> str:
        return "Tahoma" if self.rtl else "Calibri"

    @property
    def count_text(self) -> str:
        return f"{len(self.rows):,} رکورد" if self.rtl else f"{len(self.rows):,} records"


@dataclass(frozen=True, slots=True)
class Colors:
    ink: str
    cover: str
    accent: str
    band: str
    edge: str
    paper: str


PALETTES = {
    "formal": Colors("192F49", "1E3552", "266673", "F1F5F8", "D8E2EA", "FFFFFF"),
    "modern": Colors("332147", "462568", "6E3BBA", "F2EAFB", "DED0F1", "FAF6FE"),
}
DOC_TYPES = (".xlsx", ".docx", ".pptx", ".csv")
P = "http://schemas.openxmlformats.org/presentationml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
BAD_TEXT = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\uD800-\uDFFF\uFFFE\uFFFF]")


def _text(value: str) -> str:
    return escape(BAD_TEXT.sub("", value))


def export(report: Report, destination: Path) -> None:
    suffix = destination.suffix.lower()
    if suffix not in DOC_TYPES:
        raise ValueError("Unsupported report extension")
    if not report.headings:
        raise ValueError("A report needs at least one column")
    fd, filename = mkstemp(dir=destination.parent, prefix=".niro-report-", suffix=suffix)
    os.close(fd)
    temporary = Path(filename)
    try:
        writers = {".xlsx": _xlsx, ".docx": _docx, ".pptx": _pptx, ".csv": _csv}
        writers[suffix](report, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _csv(report: Report, path: Path) -> None:
    def cell(text: str) -> str:
        # Spreadsheet programs may otherwise interpret imported values as formulas.
        start = text.lstrip()
        return "'" + text if start.startswith(("=", "+", "-", "@")) or text.startswith(("\t", "\r", "\n")) else text

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(map(cell, report.headings))
        writer.writerows(tuple(map(cell, row)) for row in report.rows)


def _xlsx(report: Report, path: Path) -> None:
    import xlsxwriter

    palette = PALETTES[report.style]
    with xlsxwriter.Workbook(path, {
        "constant_memory": True, "strings_to_formulas": False, "strings_to_urls": False, "tmpdir": str(path.parent),
    }) as book:
        book.set_properties({"title": report.title, "author": "Niro Desk"})
        def fmt(**properties):
            return book.add_format({"font_name": report.font, "valign": "vcenter", "align": "right" if report.rtl else "left", **properties})

        title = fmt(bold=True, font_size=19, bg_color="#" + palette.cover, font_color="white")
        summary = fmt(bold=True, font_size=11, bg_color="#" + palette.band, font_color="#" + palette.ink)
        header = fmt(bold=True, text_wrap=True, bg_color="#" + palette.accent, font_color="white")
        even = fmt(text_wrap=True, bg_color="#" + palette.paper, font_color="#" + palette.ink, num_format="@")
        odd = fmt(text_wrap=True, bg_color="#" + palette.band, font_color="#" + palette.ink, num_format="@")
        sheet_size = 1_048_573
        iterator = iter(report.rows)
        tab = "گزارش" if report.rtl else "Report"
        for batch in range(max(1, (len(report.rows) + sheet_size - 1) // sheet_size)):
            sheet = book.add_worksheet(tab if not batch else f"{tab} {batch + 1}")
            if report.rtl:
                sheet.right_to_left()
            sheet.hide_gridlines(2)
            sheet.freeze_panes(3, 0)
            sheet.set_row(0, 35, title)
            sheet.set_row(1, 24, summary)
            sheet.set_row(2, 34, header)
            sheet.merge_range(0, 0, 0, max(1, len(report.headings) - 1), report.title, title)
            sheet.write_string(1, 0, report.count_text, summary)
            for column, heading in enumerate(report.headings):
                sheet.set_column(column, column, min(37, max(18, 6 + len(heading))))
                sheet.write_string(2, column, heading, header)
            last = 2
            for last, values in enumerate(islice(iterator, sheet_size), start=3):
                style = even if last % 2 else odd
                for column, value in enumerate(values):
                    sheet.write_string(last, column, value, style)
            if last > 2:
                sheet.autofilter(2, 0, last, len(report.headings) - 1)


def _word_para(text: str, report: Report, color: str, size: int, bold: bool = False) -> str:
    bidi = '<w:bidi/>' if report.rtl else ""
    rtl = '<w:rtl/>' if report.rtl else ""
    weight = '<w:b/><w:bCs/>' if bold else ""
    align = "right" if report.rtl else "left"
    body = "<w:br/>".join(f'<w:t xml:space="preserve">{_text(line)}</w:t>' for line in text.replace("\r", "").split("\n"))
    return (
        f'<w:p><w:pPr>{bidi}<w:spacing w:after="36"/><w:jc w:val="{align}"/></w:pPr>'
        f'<w:r><w:rPr><w:rFonts w:ascii="{report.font}" w:hAnsi="{report.font}" w:cs="{report.font}"/>'
        f'{weight}<w:color w:val="{color}"/><w:sz w:val="{size}"/><w:szCs w:val="{size}"/>{rtl}'
        f'</w:rPr>{body}</w:r></w:p>'
    )


def _word_row(values, report, colors, widths, index: int) -> str:
    header = index == 0
    fill = colors.cover if header else (colors.band if index % 2 else colors.paper)
    ink = "FFFFFF" if header else colors.ink
    repeat = "<w:trPr><w:tblHeader/></w:trPr>" if header else ""
    parts = [f"<w:tr>{repeat}"]
    for value, width in zip(values, widths):
        parts.append(
            f'<w:tc><w:tcPr><w:tcW w:w="{width}" w:type="dxa"/><w:shd w:fill="{fill}"/></w:tcPr>'
            + _word_para(value, report, ink, 19, header) + "</w:tc>"
        )
    return "".join(parts) + "</w:tr>"


def _docx(report: Report, path: Path) -> None:
    from docx import Document
    from docx.enum.section import WD_ORIENT
    from docx.shared import Inches

    source = BytesIO()
    template = Document()
    section = template.sections[0]
    section.orientation = WD_ORIENT.LANDSCAPE
    section.page_width, section.page_height = Inches(11.7), Inches(8.3)
    section.left_margin = section.right_margin = Inches(0.55)
    section.top_margin = section.bottom_margin = Inches(0.6)
    template.core_properties.title = report.title
    template.core_properties.author = "Niro Desk"
    template.save(source)
    colors = PALETTES[report.style]
    index_title = "ردیف" if report.rtl else "Row"

    with ZipFile(source) as base, ZipFile(path, "w", ZIP_DEFLATED) as result:
        for item in base.infolist():
            if item.filename != "word/document.xml":
                result.writestr(item, base.read(item))
        beginning, marker, ending = base.read("word/document.xml").partition(b"<w:sectPr")
        with result.open("word/document.xml", "w") as out:
            out.write(beginning)
            def write(markup):
                out.write(markup.encode("utf-8"))

            write(_word_para(report.title, report, colors.cover, 44, True))
            write(_word_para(report.count_text, report, colors.accent, 23, True))
            for start in range(0, len(report.headings), 7):
                index = start // 7
                headings = (index_title, *report.headings[start:start + 7])
                if index:
                    write('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
                if len(report.headings) > 7:
                    label = f"بخش {index + 1}" if report.rtl else f"Section {index + 1}"
                    write(_word_para(label, report, colors.accent, 23, True))
                widths = (950, *((14200 // (len(headings) - 1),) * (len(headings) - 1)))
                bidi = "<w:bidiVisual/>" if report.rtl else ""
                grid = "".join(f'<w:gridCol w:w="{width}"/>' for width in widths)
                split = "".join(f'<w:{side} w:val="single" w:sz="4" w:color="{colors.edge}"/>' for side in ("top", "left", "bottom", "right", "insideH", "insideV"))
                write(
                    f'<w:tbl><w:tblPr>{bidi}<w:tblW w:w="{sum(widths)}" w:type="dxa"/><w:tblBorders>{split}</w:tblBorders>'
                    f'<w:tblLayout w:type="fixed"/></w:tblPr><w:tblGrid>{grid}</w:tblGrid>'
                )
                write(_word_row(headings, report, colors, widths, 0))
                for number, row in enumerate(report.rows, 1):
                    write(_word_row((str(number), *row[start:start + 7]), report, colors, widths, number))
                write("</w:tbl>")
            out.write(marker + ending)


def _a_body(text: str, report: Report, color: str, size: int, bold: bool = False) -> str:
    rtl = ' rtl="1" algn="r"' if report.rtl else ""
    weight = ' b="1"' if bold else ""
    paras = []
    for line in text.replace("\r", "").split("\n"):
        paras.append(
            f'<a:p><a:pPr{rtl}/><a:r><a:rPr lang="{"fa-IR" if report.rtl else "en-US"}" sz="{size}"{weight}>'
            f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill><a:latin typeface="{report.font}"/>'
            f'<a:cs typeface="{report.font}"/></a:rPr><a:t>{_text(line)}</a:t></a:r></a:p>'
        )
    return '<a:bodyPr wrap="square" lIns="0" rIns="0" tIns="0" bIns="0"/><a:lstStyle/>' + "".join(paras)


def _shape(identifier: int, x: int, y: int, width: int, height: int, body: str) -> str:
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{identifier}" name="Text {identifier}"/><p:cNvSpPr txBox="1"/><p:nvPr/>'
        f'</p:nvSpPr><p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{width}" cy="{height}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom><a:noFill/></p:spPr><p:txBody>{body}</p:txBody></p:sp>'
    )


def _slide(report: Report, headings: tuple[str, ...], rows: list, page: int, section: int) -> bytes:
    colors = PALETTES[report.style]
    table_width = 11_176_000
    widths = (850_000, *((table_width - 850_000) // (len(headings) - 1),) * (len(headings) - 1))
    if report.rtl:
        widths = widths[::-1]
    title = _shape(2, 510000, 355000, table_width, 680000, _a_body(report.title, report, colors.cover, 2900, True))
    suffix = f"بخش {section}   ·   صفحهٔ {page}" if report.rtl else f"Section {section}   ·   Page {page}"
    meta = _shape(3, 510000, 1100000, table_width, 450000, _a_body(report.count_text + "   ·   " + suffix, report, colors.accent, 1500, True))
    grid = "".join(f'<a:gridCol w="{width}"/>' for width in widths)
    data = [(0, headings, 1), *rows]
    table_rows = []
    height = 0
    for index, values, lines in data:
        if report.rtl:
            values = values[::-1]
        header = index == 0
        fill = colors.cover if header else (colors.band if index % 2 else colors.paper)
        ink = "FFFFFF" if header else colors.ink
        row_h = 490000 if header else 157000 + 215000 * lines
        height += row_h
        cells = "".join(
            f'<a:tc><a:txBody>{_a_body(text, report, ink, 1350 if header else 1150, header)}</a:txBody>'
            f'<a:tcPr marL="95000" marR="95000" marT="36000" marB="36000" anchor="ctr">'
            f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill></a:tcPr></a:tc>'
            for text in values
        )
        table_rows.append(f'<a:tr h="{row_h}">{cells}</a:tr>')
    table = (
        f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="4" name="Report table"/><p:cNvGraphicFramePr/><p:nvPr/>'
        f'</p:nvGraphicFramePr><p:xfrm><a:off x="510000" y="1650000"/><a:ext cx="{sum(widths)}" cy="{height}"/></p:xfrm>'
        f'<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table"><a:tbl><a:tblPr/><a:tblGrid>{grid}</a:tblGrid>'
        + "".join(table_rows) + "</a:tbl></a:graphicData></a:graphic></p:graphicFrame>"
    )
    markup = (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><p:sld xmlns:p="{P}" xmlns:a="{A}" xmlns:r="{R}">'
        f'<p:cSld><p:bg><p:bgPr><a:solidFill><a:srgbClr val="{colors.paper}"/></a:solidFill><a:effectLst/></p:bgPr></p:bg>'
        f'<p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/>'
        f'{title}{meta}{table}</p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>'
    )
    return markup.encode("utf-8")


def _slide_rows(report: Report, start: int):
    line_width = 19 if report.rtl else 24
    for index, row in enumerate(report.rows, 1):
        wrapped = [
            [chunk for line in value.splitlines() for chunk in (textwrap.wrap(line, line_width, replace_whitespace=False) or [""])]
            for value in row[start:start + 5]
        ]
        total = max((len(column) for column in wrapped), default=0)
        for offset in range(0, max(1, total), 3):
            segment = tuple("\n".join(column[offset:offset + 3]) for column in wrapped)
            yield index, (str(index) if not offset else f"{index} •", *segment), max(1, min(3, total - offset))


def _pptx(report: Report, path: Path) -> None:
    from pptx import Presentation
    from pptx.util import Inches

    source = BytesIO()
    base = Presentation()
    base.slide_width = Inches(13.333)
    base.slide_height = Inches(7.5)
    base.core_properties.title = report.title
    base.core_properties.author = "Niro Desk"
    base.save(source)
    presentation, relationships, content_types = "ppt/presentation.xml", "ppt/_rels/presentation.xml.rels", "[Content_Types].xml"
    rel_template = (
        f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{REL}">'
        f'<Relationship Id="rId1" Type="{R}/slideLayout" Target="../slideLayouts/slideLayout7.xml"/></Relationships>'
    )
    with ZipFile(source) as template, ZipFile(path, "w", ZIP_DEFLATED) as result:
        for info in template.infolist():
            if info.filename not in {presentation, relationships, content_types}:
                result.writestr(info, template.read(info))
        pres = ET.fromstring(template.read(presentation))
        rels = ET.fromstring(template.read(relationships))
        types = ET.fromstring(template.read(content_types))
        slide_ids = pres.find(f"{{{P}}}sldIdLst")
        if slide_ids is None:
            slide_ids = ET.Element(f"{{{P}}}sldIdLst")
            pres.find(f"{{{P}}}sldSz").addprevious(slide_ids)

        page = 0
        def write_slide(headings, rows, section):
            nonlocal page
            page += 1
            slide_name = f"slide{page}.xml"
            relationship = f"rIdNiro{page}"
            ET.SubElement(slide_ids, f"{{{P}}}sldId", {"id": str(255 + page), f"{{{R}}}id": relationship})
            ET.SubElement(rels, f"{{{REL}}}Relationship", Id=relationship, Type=f"{R}/slide", Target=f"slides/{slide_name}")
            ET.SubElement(types, f"{{{CT}}}Override", PartName=f"/ppt/slides/{slide_name}", ContentType="application/vnd.openxmlformats-officedocument.presentationml.slide+xml")
            result.writestr(f"ppt/slides/{slide_name}", _slide(report, headings, rows, page, section))
            result.writestr(f"ppt/slides/_rels/{slide_name}.rels", rel_template)
        for start in range(0, len(report.headings), 5):
            section = start // 5 + 1
            headings = ("ردیف" if report.rtl else "Row", *report.headings[start:start + 5])
            rows, height = [], 0
            for row in _slide_rows(report, start):
                needed = 157000 + 215000 * row[-1]
                if rows and height + needed > 4_420_000:
                    write_slide(headings, rows, section)
                    rows, height = [], 0
                rows.append(row)
                height += needed
            write_slide(headings, rows, section)
        for filename, root in ((presentation, pres), (relationships, rels), (content_types, types)):
            result.writestr(filename, ET.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True))
