"""
Turns uploaded Word (.docx), Excel (.xlsx) and PowerPoint (.pptx) files into safe, readable HTML so staff
can read training materials right in the ERP instead of downloading them. Uses only the Python standard
library (these file types are zip files full of XML), so nothing new has to be installed.

Everything pulled out of a file is HTML-escaped, so a document can never inject anything into a page.
Pictures, charts and fancy layout are not shown; the original is always one click away to download.
"""
import datetime as _dt
import html
import io
import re
import zipfile
import xml.etree.ElementTree as ET

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
A = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

READABLE_EXTENSIONS = {"docx", "xlsx", "pptx"}
MAX_SHEET_ROWS = 600
MAX_SHEET_COLS = 30


def esc(s):
    return html.escape(s or "", quote=True)


def _zip(data):
    return zipfile.ZipFile(io.BytesIO(data))


def _xml(z, name):
    try:
        return ET.fromstring(z.read(name))
    except KeyError:
        return None


# ---------------------------------------------------------------- Word

def _numbering_kinds(z):
    """{numId: {ilvl: 'ol' or 'ul'}} from word/numbering.xml (defaults to bullets)."""
    root = _xml(z, "word/numbering.xml")
    kinds = {}
    if root is None:
        return kinds
    abstract = {}
    for an in root.findall(W + "abstractNum"):
        lv = {}
        for l in an.findall(W + "lvl"):
            fmt = l.find(W + "numFmt")
            lv[int(l.get(W + "ilvl", "0"))] = "ul" if fmt is not None and fmt.get(W + "val") == "bullet" else "ol"
        abstract[an.get(W + "abstractNumId")] = lv
    for n in root.findall(W + "num"):
        aid = n.find(W + "abstractNumId")
        if aid is not None:
            kinds[n.get(W + "numId")] = abstract.get(aid.get(W + "val"), {})
    return kinds


def _runs_html(p):
    out = []
    for r in p.iter(W + "r"):
        rpr = r.find(W + "rPr")
        bold = rpr is not None and rpr.find(W + "b") is not None and rpr.find(W + "b").get(W + "val") not in ("0", "false")
        ital = rpr is not None and rpr.find(W + "i") is not None and rpr.find(W + "i").get(W + "val") not in ("0", "false")
        text = []
        for el in r:
            if el.tag == W + "t":
                text.append(esc(el.text))
            elif el.tag == W + "tab":
                text.append("&emsp;")
            elif el.tag in (W + "br", W + "cr"):
                text.append("<br>")
        s = "".join(text)
        if not s:
            continue
        if bold:
            s = f"<strong>{s}</strong>"
        if ital:
            s = f"<em>{s}</em>"
        out.append(s)
    return "".join(out)


def _paragraph(p, kinds):
    """-> (kind, level, html) where kind is 'h1'..'h3', 'p', or a list kind 'ul'/'ol'."""
    ppr = p.find(W + "pPr")
    style = ""
    num = None
    if ppr is not None:
        ps = ppr.find(W + "pStyle")
        style = (ps.get(W + "val") if ps is not None else "") or ""
        num = ppr.find(W + "numPr")
    text = _runs_html(p)
    sl = style.lower().replace(" ", "")
    if sl == "title":
        return "h2", 0, text
    m = re.match(r"heading([1-9])", sl)
    if m and text.strip():
        return f"h{min(int(m.group(1)), 3) + 1}", 0, text
    if num is not None and text.strip():
        nid = num.find(W + "numId")
        il = num.find(W + "ilvl")
        level = int(il.get(W + "val")) if il is not None else 0
        kind = kinds.get(nid.get(W + "val") if nid is not None else None, {}).get(level, "ul")
        return kind, level, text
    return "p", 0, text


def _flow(container, kinds):
    """Converts paragraphs/tables inside a body or table cell to HTML."""
    out = []
    stack = []  # open list kinds

    def close_lists(to=0):
        while len(stack) > to:
            out.append(f"</{stack.pop()}>")

    for el in container:
        if el.tag == W + "p":
            kind, level, text = _paragraph(el, kinds)
            if kind in ("ul", "ol"):
                while len(stack) < level + 1:
                    out.append(f"<{kind}>")
                    stack.append(kind)
                close_lists(level + 1)
                out.append(f"<li>{text}</li>")
                continue
            close_lists()
            if not text.strip():
                out.append('<p class="doc-gap">&nbsp;</p>')
            elif kind.startswith("h"):
                out.append(f"<{kind}>{text}</{kind}>")
            else:
                out.append(f"<p>{text}</p>")
        elif el.tag == W + "tbl":
            close_lists()
            rows = []
            for tr in el.findall(W + "tr"):
                cells = []
                for tc in tr.findall(W + "tc"):
                    cells.append(f"<td>{_flow(tc, kinds)}</td>")
                rows.append("<tr>" + "".join(cells) + "</tr>")
            out.append('<table class="doc-table">' + "".join(rows) + "</table>")
        elif el.tag == W + "sdt":
            content = el.find(W + "sdtContent")
            if content is not None:
                close_lists()
                out.append(_flow(content, kinds))
    close_lists()
    return "".join(out)


def docx_to_html(data):
    z = _zip(data)
    root = _xml(z, "word/document.xml")
    if root is None:
        raise ValueError("Not a readable Word file.")
    body = root.find(W + "body")
    return _flow(body, _numbering_kinds(z))


# ---------------------------------------------------------------- PowerPoint

def pptx_to_html(data):
    z = _zip(data)
    names = [n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
    names.sort(key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
    out = []
    for i, n in enumerate(names, start=1):
        root = ET.fromstring(z.read(n))
        paras = []
        for p in root.iter(A + "p"):
            t = "".join(x.text or "" for x in p.iter(A + "t")).strip()
            if t:
                paras.append(f"<p>{esc(t)}</p>")
        out.append(f'<div class="doc-slide"><h3>Slide {i}</h3>{"".join(paras) or "<p class=muted>(no text)</p>"}</div>')
    return "".join(out) or "<p>No slides found.</p>"


# ---------------------------------------------------------------- Excel

_BUILTIN_DATE = {14, 15, 16, 17}
_BUILTIN_TIME = {18, 19, 20, 21, 45, 46}
_BUILTIN_DATETIME = {22, 47}


def _col_index(ref):
    letters = re.match(r"[A-Z]+", ref).group(0)
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _fmt_kind(fmt_id, code):
    if fmt_id in _BUILTIN_DATE:
        return "date"
    if fmt_id in _BUILTIN_TIME:
        return "time"
    if fmt_id in _BUILTIN_DATETIME:
        return "datetime"
    if code:
        c = re.sub(r'"[^"]*"|\[[^\]]*\]|\\.', "", code.lower())
        has_d = bool(re.search(r"[dy]", c)) or "mmm" in c
        has_t = bool(re.search(r"h|s", c)) or "am/pm" in c
        if has_d and has_t:
            return "datetime"
        if has_d:
            return "date"
        if has_t:
            return "time"
    return None


def _style_kinds(z):
    root = _xml(z, "xl/styles.xml")
    if root is None:
        return {}
    custom = {int(n.get("numFmtId")): n.get("formatCode") for n in root.iter(S + "numFmt")}
    kinds = {}
    xfs = root.find(S + "cellXfs")
    if xfs is not None:
        for i, xf in enumerate(xfs.findall(S + "xf")):
            fid = int(xf.get("numFmtId", "0"))
            kinds[i] = _fmt_kind(fid, custom.get(fid))
    return kinds


def _format_serial(value, kind):
    try:
        v = float(value)
    except ValueError:
        return value
    base = _dt.datetime(1899, 12, 30)
    d = base + _dt.timedelta(days=v)
    if kind == "time":
        d = base + _dt.timedelta(days=v % 1)
        return _twelve(d)
    if kind == "date":
        return d.strftime("%b %-d, %Y")
    return d.strftime("%b %-d, %Y ") + _twelve(d)


def _twelve(d):
    h = d.hour % 12 or 12
    return f"{h}:{d.minute:02d} {'AM' if d.hour < 12 else 'PM'}"


def xlsx_to_html(data):
    z = _zip(data)
    shared = []
    sroot = _xml(z, "xl/sharedStrings.xml")
    if sroot is not None:
        for si in sroot.findall(S + "si"):
            shared.append("".join(t.text or "" for t in si.iter(S + "t")))
    kinds = _style_kinds(z)
    wb = _xml(z, "xl/workbook.xml")
    rels = _xml(z, "xl/_rels/workbook.xml.rels")
    target = {}
    if rels is not None:
        for r in rels:
            target[r.get("Id")] = r.get("Target")
    out = []
    sheets = wb.find(S + "sheets") if wb is not None else None
    for sh in (sheets if sheets is not None else []):
        name = sh.get("name")
        t = target.get(sh.get(R + "id"), "")
        path = t.lstrip("/") if t.startswith("/") else "xl/" + t
        root = _xml(z, path)
        if root is None:
            continue
        rows_html = []
        total_rows = 0
        for row in root.iter(S + "row"):
            total_rows += 1
            if len(rows_html) >= MAX_SHEET_ROWS:
                continue
            cells = {}
            for c in row.findall(S + "c"):
                ref = c.get("r")
                if not ref:
                    continue
                col = _col_index(ref)
                if col >= MAX_SHEET_COLS:
                    continue
                typ = c.get("t")
                v = c.find(S + "v")
                val = v.text if v is not None else None
                if typ == "s" and val is not None:
                    val = shared[int(val)] if int(val) < len(shared) else ""
                elif typ == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(S + "t"))
                elif typ == "b":
                    val = "TRUE" if val == "1" else "FALSE"
                elif val is not None and typ in (None, "n"):
                    kind = kinds.get(int(c.get("s", "0")))
                    if kind:
                        val = _format_serial(val, kind)
                    else:
                        try:
                            f = float(val)
                            val = str(int(f)) if f == int(f) else ("%.6g" % f)
                        except ValueError:
                            pass
                if val not in (None, ""):
                    cells[col] = val
            if not cells:
                rows_html.append("<tr><td>&nbsp;</td></tr>")
                continue
            width = max(cells) + 1
            rows_html.append("<tr>" + "".join(f"<td>{esc(cells.get(i, ''))}</td>" for i in range(width)) + "</tr>")
        more = ""
        if total_rows > MAX_SHEET_ROWS:
            more = f'<p class="muted">Showing the first {MAX_SHEET_ROWS} of {total_rows} rows. Download the file to see everything.</p>'
        out.append(f'<h3>{esc(name)}</h3><div class="doc-scroll"><table class="doc-table">{"".join(rows_html)}</table></div>{more}')
    return "".join(out) or "<p>This workbook is empty.</p>"


# ---------------------------------------------------------------- entry point

def extension(filename):
    return filename.rsplit(".", 1)[1].lower() if filename and "." in filename else ""


def to_html(data, filename):
    """Returns readable HTML for the file, or None when this kind of file can't be shown in the page."""
    ext = extension(filename)
    if ext not in READABLE_EXTENSIONS:
        return None
    try:
        return {"docx": docx_to_html, "xlsx": xlsx_to_html, "pptx": pptx_to_html}[ext](data)
    except (zipfile.BadZipFile, ET.ParseError, ValueError, KeyError, IndexError):
        return None
