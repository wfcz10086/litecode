---
name: pdf-reading
description: "Use this skill when you need to read, inspect, or extract content from PDF files — especially when file content is NOT in your context and you need to read it from disk. Covers content inventory, text extraction, page rasterization for visual inspection, embedded image/attachment/table/form-field extraction, and choosing the right reading strategy for different document types (text-heavy, scanned, slide-decks, forms, data-heavy). Do NOT use this skill for PDF creation, form filling, merging, splitting, watermarking, or encryption — use the pdf skill instead."
license: Proprietary. LICENSE.txt has complete terms
aliases: ["读取pdf", "提取pdf", "解析pdf"]
---

# PDF Processing Guide

## Overview

This guide covers essential PDF reading operations using Python libraries and command-line tools. For advanced features (pypdfium2 rendering, pdfplumber table settings, OCR fallback, encrypted/corrupted PDF handling), see REFERENCE.md.

## Reading & Inspecting PDFs

Before doing anything with a PDF, understand what you're working with.

### Content inventory

Run a quick diagnostic first. For simple tasks ("summarize this
document"), `pdfinfo` + a text sample may suffice. For anything
involving figures, attachments, or extraction issues, run the full set:

```bash
# Always: page count, file size, PDF version, metadata
pdfinfo document.pdf

# Always: quick text extraction check — is this a text PDF or a scan?
pdftotext -f 1 -l 1 document.pdf - | head -20

# If figures/charts may matter:
pdfimages -list document.pdf

# If the PDF might contain embedded files (reports, portfolios):
pdfdetach -list document.pdf

# If text extraction looks garbled:
pdffonts document.pdf
```

This tells you:
- **Page count and size** — how big is the job?
- **Text extractability** — does `pdftotext` return real text, or is
  it empty (scanned) or garbled (broken font encoding)?
- **Embedded raster images** — are there photos or raster figures?
  (Note: vector-drawn charts from matplotlib/Excel won't appear — see
  "Extracting embedded images" below)
- **Attachments** — are there embedded spreadsheets, data files, etc.?
- **Font status** — are fonts embedded? If not, text extraction may
  produce wrong characters.

### Text extraction

**pypdf** for basic text:
```python
from pypdf import PdfReader

reader = PdfReader("document.pdf")
print(f"Pages: {len(reader.pages)}")

# Extract text
text = ""
for page in reader.pages:
    text += page.extract_text()
```

**pdftotext** preserving layout (better for multi-column docs):
```bash
# Layout mode preserves spatial positioning
pdftotext -layout document.pdf output.txt

# Specific page range
pdftotext -f 1 -l 5 document.pdf output.txt
```

**pdfplumber** for layout-aware extraction with positioning data:
```python
import pdfplumber

with pdfplumber.open("document.pdf") as pdf:
    for page in pdf.pages:
        text = page.extract_text()
        print(text)
```

### Visual inspection (rasterize pages)

Text extraction is **blind** to charts, diagrams, figures, equations,
multi-column layout, and form structures. When any of these matter,
rasterize the relevant page and look at it with `vision_ocr`:

```bash
# Rasterize a single page (page 3 here) at 150 DPI
pdftoppm -jpeg -r 150 -f 3 -l 3 document.pdf /tmp/page

# pdftoppm zero-pads the output filename based on TOTAL page count
# (e.g., page-03.jpg for a 50-page PDF, page-003.jpg for 200+ pages)
# Don't guess the filename — find it:
ls /tmp/page-*.jpg
```

Then call `vision_ocr(path="/tmp/page-03.jpg")` (default `mode=auto`;
`mode="ocr"` for text only, `mode="describe"` for charts/figures). Several
pages at once: `vision_ocr(paths=[...])`, **max 8 images per call**, and
for text-dense pages keep it to 2–3 per call with `max_tokens=8192`
(all images share one output budget). If the active model cannot see
images, vision_ocr routes to the configured vision fallback model
automatically.

**When to rasterize vs. text-extract:**
- **Content/data questions → text extraction** (cheaper, searchable)
- **Figures, charts, visual layout → rasterize the page**
- **Tables → try text extraction first, rasterize if garbled**
- **Precision matters → do both** (extract text AND rasterize; use text
  for data, image for context)

**Cost awareness:** each vision_ocr call is a separate vision-model
request (seconds each, 120 s timeout). Only rasterize pages that matter
for the question at hand.

### Choosing your reading strategy

**Text-heavy documents** (reports, articles, books):
→ Text extraction is primary. Rasterize only for specific figures or
  pages where layout matters.

**Scanned documents** (no extractable text):
→ Rasterize pages (150–200 DPI) and run `vision_ocr` in batches. For
  converting a whole scanned PDF to Markdown, `load_skill('pdf-ocr-pipeline')`
  (tesseract is usually not installed — don't rely on pytesseract).

**Slide-deck PDFs** (exported presentations):
→ Every page is primarily visual. Rasterize individual pages on demand.
  Text extraction gives you bullet-point text but loses all layout.

**Form-heavy documents**:
→ Extract form field values programmatically first (see below). Rasterize
  the form page for visual context if needed.

**Data-heavy documents** (tables, charts, figures):
→ Use pdfplumber for tables. Rasterize pages with charts/figures.
  Extract text for surrounding narrative. Consider both text AND image
  for the same page when precision matters.

### Extracting embedded images

```bash
# List all embedded images with metadata (size, color, compression)
pdfimages -list document.pdf

# Extract all images as PNG
pdfimages -png document.pdf /tmp/img

# Extract from specific pages only (pages 3-5)
pdfimages -png -f 3 -l 5 document.pdf /tmp/img

# Extract in original format (JPEG stays JPEG, etc.)
pdfimages -all document.pdf /tmp/img
```

Then `vision_ocr(paths=["/tmp/img-000.png", ...])` to see the extracted images (≤8 per call).

**Gotcha — vector graphics:** `pdfimages` extracts only raster image
data. Charts and diagrams drawn as vector graphics (common in
matplotlib, Excel, and R exports) will NOT appear — they are page
content operators, not image objects. For these, rasterize the whole
page with `pdftoppm` instead.

**Gotcha — empty images:** `pdfimages` sometimes produces many tiny or
empty image files — these are typically background masks, transparency
layers, or decorative elements. Filter by file size to find the real
content images.

Programmatic extraction with position data:
```python
import fitz  # PyMuPDF

doc = fitz.open("document.pdf")
for page in doc:
    for img in page.get_images():
        xref = img[0]
        pix = fitz.Pixmap(doc, xref)
        if pix.n - pix.alpha > 3:  # CMYK or other non-RGB
            pix = fitz.Pixmap(fitz.csRGB, pix)
        pix.save(f"/tmp/img_{xref}.png")
```

### Extracting file attachments

PDFs can contain embedded files — spreadsheets, data files, other
documents. Common in business reports, PDF portfolios, and PDF/A-3
compliance documents.

```bash
# List all attachments
pdfdetach -list document.pdf

# Extract all attachments to a directory
mkdir -p /tmp/attachments
pdfdetach -saveall -o /tmp/attachments/ document.pdf

# Extract a specific attachment by number (1-based index from -list output)
pdfdetach -save 1 -o /tmp/attachment.pdf document.pdf
```

In Python: `PdfReader(path).attachments` (dict name → [bytes]); sanitize names with `os.path.basename`.

### Extracting form field data

PDFs with interactive forms (government forms, applications, contracts)
have fillable fields whose values can be read programmatically:

```python
from pypdf import PdfReader

reader = PdfReader("form.pdf")

# Text input fields only:
fields = reader.get_form_text_fields()
for name, value in fields.items():
    print(f"{name}: {value}")

# All field types (checkboxes, radio buttons, dropdowns too):
all_fields = reader.get_fields() or {}
for name, field in all_fields.items():
    print(f"{name}: {field.get('/V', '')} (type: {field.get('/FT', '')})")
```

`get_form_text_fields()` returns only text input fields. For
government forms and contracts that use checkboxes, radio buttons,
and dropdowns, use `get_fields()` instead to see all field types.

For comprehensive field info (types, options, defaults):
```bash
pdftk form.pdf dump_data_fields
```

For anything beyond reading form data — filling forms, creating forms —
use `load_skill('pdf')`.

### Audio, video, 3D

Rare. Check `pdfdetach -list`; if not an attachment it's a Rich Media
annotation — iterate annotations with PyMuPDF.

### Font diagnostics

If text extraction produces garbled output (wrong characters, missing
text, mojibake), check the font situation:

```bash
pdffonts document.pdf
```

Look at the "emb" column — if fonts show "no" (not embedded) with
custom encodings, the PDF's character mapping may be broken for text
extraction. In that case, rasterize the page and use vision instead.


---

## Quick Reference

| Task | Best Tool | Command/Code |
|------|-----------|--------------|
| Inspect PDF | poppler-utils | `pdfinfo`, `pdfimages -list`, `pdfdetach -list`, `pdffonts` |
| Extract text | pdfplumber | `page.extract_text()` |
| Extract text (CLI) | pdftotext | `pdftotext -layout input.pdf output.txt` |
| Extract tables | pdfplumber | `page.extract_tables()` |
| See page visually | pdftoppm | `pdftoppm -jpeg -r 150 -f N -l N` |
| Extract images | pdfimages | `pdfimages -png input.pdf prefix` |
| Extract attachments | pdfdetach | `pdfdetach -saveall -o /tmp/` |
| Read form fields | pypdf | `reader.get_fields()` |
| OCR scanned PDFs | vision_ocr | rasterize → `vision_ocr(paths=[...])` ≤8/call |

## PDF Form Filling, Creation, Merging, Splitting, and Other Operations

This skill covers **reading and inspection** only. For filling forms,
creating, merging, splitting, rotating, watermarking, encrypting, or
other PDF manipulation tasks, use `load_skill('pdf')`.
