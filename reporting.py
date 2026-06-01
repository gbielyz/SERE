import html
import io
import textwrap
import zipfile


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_MIME = "application/pdf"


def xlsx_workbook(sheets):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", _content_types(len(sheets)))
        zf.writestr("_rels/.rels", _root_rels())
        zf.writestr("xl/workbook.xml", _workbook_xml(sheets))
        zf.writestr("xl/_rels/workbook.xml.rels", _workbook_rels(len(sheets)))
        zf.writestr("xl/styles.xml", _styles_xml())
        for index, (_name, rows) in enumerate(sheets, 1):
            zf.writestr(f"xl/worksheets/sheet{index}.xml", _worksheet_xml(rows))
    buffer.seek(0)
    return buffer


def pdf_report(title, sections):
    lines = [title, ""]
    for section_title, rows in sections:
        lines.append(section_title)
        lines.append("-" * min(72, len(section_title)))
        for row in rows:
            lines.extend(_wrap_line(row))
        lines.append("")
    return _simple_pdf(lines)


def _wrap_line(line):
    text = "" if line is None else str(line)
    wrapped = textwrap.wrap(text, width=104) or [""]
    return wrapped


def _content_types(sheet_count):
    sheet_overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i}.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        for i in range(1, sheet_count + 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/styles.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        f"{sheet_overrides}</Types>"
    )


def _root_rels():
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )


def _workbook_xml(sheets):
    sheet_items = "".join(
        f'<sheet name="{_xml(sheet_name[:31])}" sheetId="{index}" r:id="rId{index}"/>'
        for index, (sheet_name, _rows) in enumerate(sheets, 1)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f"<sheets>{sheet_items}</sheets></workbook>"
    )


def _workbook_rels(sheet_count):
    rels = "".join(
        f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
        for i in range(1, sheet_count + 1)
    )
    rels += f'<Relationship Id="rId{sheet_count + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f"{rels}</Relationships>"
    )


def _worksheet_xml(rows):
    max_col = max((len(row) for row in rows), default=1)
    cols = _columns_xml(rows, max_col)
    auto_filter = f'<autoFilter ref="A1:{_column_name(max_col)}{max(1, len(rows))}"/>' if rows else ""
    row_xml = []
    for row_index, row in enumerate(rows, 1):
        cells = []
        for col_index, value in enumerate(row, 1):
            ref = f"{_column_name(col_index)}{row_index}"
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                style = ' s="2"' if row_index > 1 else ' s="1"'
                cells.append(f'<c r="{ref}"{style}><v>{value}</v></c>')
            else:
                style = ' s="1"' if row_index == 1 else ''
                cells.append(f'<c r="{ref}"{style} t="inlineStr"><is><t>{_xml(value)}</t></is></c>')
        row_xml.append(f'<row r="{row_index}">{"".join(cells)}</row>')
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        f"{cols}"
        f'<sheetData>{"".join(row_xml)}</sheetData>{auto_filter}</worksheet>'
    )


def _columns_xml(rows, max_col):
    widths = []
    for col in range(max_col):
        values = [str(row[col]) for row in rows if col < len(row)]
        longest = max((len(value) for value in values), default=10)
        widths.append(min(34, max(10, longest + 2)))
    col_items = "".join(
        f'<col min="{index}" max="{index}" width="{width}" customWidth="1"/>'
        for index, width in enumerate(widths, 1)
    )
    return f"<cols>{col_items}</cols>"


def _styles_xml():
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<fonts count="2">'
        '<font><sz val="11"/><color rgb="FF111827"/><name val="Calibri"/></font>'
        '<font><b/><sz val="11"/><color rgb="FFFFFFFF"/><name val="Calibri"/></font>'
        '</fonts>'
        '<fills count="3">'
        '<fill><patternFill patternType="none"/></fill>'
        '<fill><patternFill patternType="gray125"/></fill>'
        '<fill><patternFill patternType="solid"><fgColor rgb="FF1F2A44"/><bgColor indexed="64"/></patternFill></fill>'
        '</fills>'
        '<borders count="2">'
        '<border><left/><right/><top/><bottom/><diagonal/></border>'
        '<border><left style="thin"><color rgb="FFD6DAE6"/></left><right style="thin"><color rgb="FFD6DAE6"/></right><top style="thin"><color rgb="FFD6DAE6"/></top><bottom style="thin"><color rgb="FFD6DAE6"/></bottom><diagonal/></border>'
        '</borders>'
        '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
        '<cellXfs count="3">'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"/>'
        '<xf numFmtId="0" fontId="1" fillId="2" borderId="1" xfId="0" applyFont="1" applyFill="1" applyBorder="1"><alignment horizontal="center"/></xf>'
        '<xf numFmtId="0" fontId="0" fillId="0" borderId="1" xfId="0" applyBorder="1"><alignment horizontal="center"/></xf>'
        '</cellXfs>'
        '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>'
        '<dxfs count="0"/><tableStyles count="0" defaultTableStyle="TableStyleMedium2" defaultPivotStyle="PivotStyleLight16"/>'
        '</styleSheet>'
    )


def _column_name(index):
    name = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        name = chr(65 + remainder) + name
    return name


def _xml(value):
    return html.escape("" if value is None else str(value), quote=True)


def _simple_pdf(lines):
    pages = []
    current = []
    for line in lines:
        if len(current) >= 44:
            pages.append(current)
            current = []
        current.append(line)
    if current:
        pages.append(current)

    objects = []
    page_ids = []
    font_id = 3
    objects.append("<< /Type /Catalog /Pages 2 0 R >>")
    objects.append(None)
    objects.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for page in pages:
        content = _pdf_content(page)
        content_id = len(objects) + 2
        page_id = len(objects) + 1
        page_ids.append(page_id)
        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        )
        objects.append(f"<< /Length {len(content)} >>\nstream\n{content.decode('cp1252')}\nendstream")

    kids = " ".join(f"{page_id} 0 R" for page_id in page_ids)
    objects[1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>"

    output = io.BytesIO()
    output.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(output.tell())
        output.write(f"{index} 0 obj\n{obj}\nendobj\n".encode("cp1252", errors="replace"))
    xref = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode("ascii")
    )
    output.seek(0)
    return output


def _pdf_content(lines):
    content_lines = ["BT", "/F1 11 Tf", "50 800 Td", "14 TL"]
    for line in lines:
        content_lines.append(f"({_pdf_escape(line)}) Tj")
        content_lines.append("T*")
    content_lines.append("ET")
    return "\n".join(content_lines).encode("cp1252", errors="replace")


def _pdf_escape(value):
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
