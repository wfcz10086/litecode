---
name: file-reading
description: "读取和处理各种文件类型的路由技能。当用户提供文件路径、上传文件、或要求处理 workspace 中的文件时触发。覆盖 pdf/docx/xlsx/csv/json/images/archives/ebooks/代码文件。核心原则：先看扩展名→stat 大小→选择正确工具→只读需要的量。"
compatibility: "LiteCode — workspace 路径或用户指定路径"
license: Proprietary. LICENSE.txt has complete terms
---

# 读取和处理文件

## 为什么需要这个技能

用户给你文件路径时，你必须用正确的方式去读它。

`cat` 一个 PDF 会输出二进制垃圾。`cat` 一个 100MB CSV 会撑爆 context。
`cat` 一个 DOCX 会输出 ZIP 原始字节。图片 `cat` 了也没用。

这个技能告诉你每种文件的**正确第一步**，以及何时交给更专业的技能。

## 通用协议

1. **看扩展名** — 这是你的分发键
2. **先 stat 再读** — 大文件需要采样不是全读
   ```bash
   stat -c '%s bytes, %y' /tmp/openclaw_workspace/report.pdf
   file /tmp/openclaw_workspace/report.pdf
   ```
3. **只读回答问题需要的量** — 用户问"CSV 有多少行"，`wc -l` 就够了
4. **有专用技能就交给它** — 下面的表告诉你什么时候交

## extract-text 工具

对 docx/odt/epub/xlsx/pptx/rtf/ipynb，第一步用 `extract-text <file>`：
- docx/odt/epub → markdown（标题/加粗/列表/链接/表格）
- xlsx → tab 分隔行 + `## Sheet:` 标题
- pptx → `## Slide N` 标题下的文本
- ipynb → 代码 cell 用 fenced block
- rtf → 纯文本

如果 `extract-text` 报错，docx 用 `pandoc <file> -t plain` 兜底，xlsx/pptx 用 Python 库（openpyxl / python-pptx）。

## 文件类型分发表

| 扩展名 | 第一步 | 专用技能 |
|--------|--------|---------|
| `.pdf` | 内容清查（见 PDF 节） | `load_skill('pdf-reading')` |
| `.docx` | `extract-text` | `load_skill('docx')` |
| `.doc` (旧) | 先转 `.docx` | `load_skill('docx')` |
| `.xlsx` | `extract-text` | `load_skill('xlsx')` |
| `.xlsm` | `extract-text --format xlsx` | `load_skill('xlsx')` |
| `.xls` (旧) | `pd.read_excel(engine="xlrd")` | `load_skill('xlsx')` |
| `.ods` | `pd.read_excel(engine="odf")` | `load_skill('xlsx')` |
| `.pptx` | `extract-text` | `load_skill('pptx')` |
| `.ppt` (旧) | 先转 `.pptx` | `load_skill('pptx')` |
| `.csv` `.tsv` | `pandas` + `nrows` | — |
| `.json` `.jsonl` | `jq` 探结构 | — |
| `.jpg` `.png` `.gif` `.webp` | 直接描述（vision input） | — |
| `.zip` `.tar` `.tar.gz` | 只列目录，不解压 | — |
| `.gz` (单文件) | `zcat | head` | — |
| `.epub` `.odt` | `extract-text` | — |
| `.rtf` `.ipynb` | `extract-text` | — |
| `.py` `.js` `.go` 等代码 | `wc -c` → 按大小选读 | — |
| `.txt` `.md` `.log` | `wc -c` → `head`/`tail`/`cat` | — |
| 未知 | `file` + `xxd | head -5` | — |

---

## PDF

**绝对不要 `cat` PDF。**

快速第一步：
```bash
pdfinfo /tmp/openclaw_workspace/report.pdf
pdftotext -f 1 -l 1 /tmp/openclaw_workspace/report.pdf - | head -20
```

Python 方式：
```python
from pypdf import PdfReader
r = PdfReader("/tmp/openclaw_workspace/report.pdf")
print(f"{len(r.pages)} pages")
print(r.pages[0].extract_text()[:2000])
```

更复杂的场景（图表/表格/扫描件/表单）→ `load_skill('pdf-reading')`。
创建/合并/水印 PDF → `load_skill('pdf')`。

**注意**: 生成中文 PDF 时用 weasyprint（字体嵌入），不要用 reportlab + CID 字体（微信/手机阅读器会乱码）。

---

## DOCX / DOC

```bash
extract-text /tmp/openclaw_workspace/memo.docx | head -200
```

编辑/创建/跟踪修改 → `load_skill('docx')`。
旧 `.doc` 先转换 — 见 docx 技能。

---

## XLSX / XLS

快速查看：
```bash
extract-text /tmp/openclaw_workspace/data.xlsx | head -100
```

Python 结构化预览：
```python
from openpyxl import load_workbook
wb = load_workbook("/tmp/openclaw_workspace/data.xlsx", read_only=True)
print("Sheets:", wb.sheetnames)
ws = wb.active
for row in ws.iter_rows(max_row=5, values_only=True):
    print(row)
```

`read_only=True` 必须加。`ws.max_row` 在此模式下不可靠。

旧 `.xls` → `pd.read_excel("old.xls", engine="xlrd", nrows=5)`
`.ods` → `pd.read_excel("data.ods", engine="odf", nrows=5)`

公式/图表/格式化 → `load_skill('xlsx')`。

---

## CSV / TSV

**不要**裸 `cat` — 引号内大单元格会炸掉 `head -5`。
```python
import pandas as pd
df = pd.read_csv("/tmp/openclaw_workspace/data.csv", nrows=5)
print(df)
print(df.dtypes)
```

近似行数：`wc -l data.csv`（引号内换行会多算）

---

## JSON / JSONL

```bash
jq 'type' data.json
jq 'if type == "array" then length elif type == "object" then keys else . end' data.json
```

JSONL — 不要对整个文件 `jq`：
```bash
head -3 data.jsonl | jq .
wc -l data.jsonl
```

---

## 图片

微信发来的图片已在 context（vision input），不需要磁盘读取。
程序化处理时：
```python
from PIL import Image
img = Image.open("photo.jpg")
print(img.size, img.mode, img.format)
```

OCR：`pytesseract.image_to_string(img)`

---

## 压缩包

**先列目录，不解压**：
```bash
unzip -l bundle.zip
tar -tf bundle.tar.gz
```

提取单个文件：`unzip -p bundle.zip path/inside/file.txt`
单独 `.gz`：`zcat data.json.gz | head -50`

---

## 代码文件

先看大小（`wc -c`），<20KB 全读，>20KB 用 `head`+`tail`+`grep`。

**语法检查快捷方式**：
```bash
python3 -m py_compile script.py      # Python
node --check app.js                   # Node.js
go build -o /dev/null ./main.go       # Go
gcc -fsyntax-only main.c              # C
rustc --edition 2021 --crate-type lib main.rs -o /dev/null  # Rust
```

**编译并运行**：
```bash
# Python
python3 script.py

# Go
go run main.go

# Rust
cargo run

# C/C++
gcc -o prog main.c && ./prog
```

---

## 日志文件

用户通常关心末尾：
```bash
tail -200 /tmp/openclaw_workspace/app.log
```

搜索特定错误：
```bash
grep -i "error\|exception\|fatal\|panic" app.log | tail -20
```

---

## 未知扩展名

```bash
file mystery.bin
xxd mystery.bin | head -5
```

认不出来就**问用户**，不要猜。
