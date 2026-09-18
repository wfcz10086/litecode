---
name: pdf-ocr-pipeline
description: >
  把任意大小的 PDF (尤其是带图片/扫描件/混合版式) 提取为按页面顺序拼接的 Markdown.
  自动检测每页有没有文字层: 有 → pdfplumber 直接抽取; 没有 → vision_ocr 识别图片.
  最后按页号顺序合并成单一 out.md. 支持 30MB / 200+ 页大文件, 走 DAG 并行分批.
  触发词: pdf转md, pdf ocr, 扫描件识别, pdf提取, pdf to markdown, 论文pdf, 报告pdf.
---

# PDF → Markdown 有序拼接 Pipeline

处理 **任意 PDF → out.md**, 保持页面顺序, 文字 + 图片内容合二为一。

## 适用场景

- ✅ 纯文字 PDF (论文 / 小说 / 报告)
- ✅ 纯扫描件 PDF (图片化的古籍 / 合同扫描)
- ✅ 混合 PDF (有文字层 + 有嵌入图片/图表)
- ✅ 大文件 (30MB / 200+ 页, 走 DAG 并行)
- ❌ 带加密/密码的 PDF (先 `qpdf --decrypt`)

## 环境依赖

```bash
pip3 install pdfplumber pypdfium2 pillow --break-system-packages
# pdfplumber: 文字层抽取
# pypdfium2:  PDF → 页面 PNG 栅格化 (无 poppler 依赖, 比 pdf2image 省事)
```

## 核心工作流

```
┌─ 1. 探测 + 分页 ──────────────────────────────┐
│   pdfplumber.open(pdf)                       │
│   for page in pages:                         │
│     has_text = len(page.extract_text() or "") > 30 │
│     - 有文字: 抽 text, 跳过 OCR              │
│     - 没文字: page → png @ 200 DPI           │
└──────────────────────────────────────────────┘
         ↓
┌─ 2. 并行 OCR (vision_ocr) ────────────────────┐
│   批次 ≤ 5 页, 避免视觉模型并发爆                │
│   spawn_agent(parallel_tasks=[...])            │
│   每 task = vision_ocr(page_NNN.png)           │
└──────────────────────────────────────────────┘
         ↓
┌─ 3. 顺序合并 ────────────────────────────────┐
│   for i in 1..N:                             │
│     out += f"\n\n## Page {i}\n\n" + page_i   │
│   write_file(out_path, out)                  │
└──────────────────────────────────────────────┘
```

## Step 1: 写 extract.py (分页 + 分流)

```python
# write_file: /tmp/openclaw_workspace/pdf_extract.py
import pdfplumber
import pypdfium2 as pdfium
from pathlib import Path
import sys, json, os

pdf_path = sys.argv[1]
out_dir = Path(sys.argv[2])  # e.g. /tmp/openclaw_workspace/pdf_pages
out_dir.mkdir(parents=True, exist_ok=True)

text_layer = {}  # page_num -> text (非空表示有文字层)
ocr_needed = []  # page_num 列表, 待 OCR

# (a) pdfplumber 抽文字层
with pdfplumber.open(pdf_path) as pdf:
    total = len(pdf.pages)
    for i, page in enumerate(pdf.pages, start=1):
        text = (page.extract_text() or "").strip()
        if len(text) > 30:
            text_layer[i] = text
        else:
            ocr_needed.append(i)

# (b) 无文字层的页面 → PNG @ 200 DPI
if ocr_needed:
    doc = pdfium.PdfDocument(pdf_path)
    for i in ocr_needed:
        page = doc[i - 1]
        bitmap = page.render(scale=200 / 72)  # 200 DPI
        bitmap.to_pil().save(out_dir / f"page_{i:03d}.png")

meta = {
    "total": total,
    "text_pages": len(text_layer),
    "ocr_pages": len(ocr_needed),
    "text_layer": text_layer,
    "ocr_needed": ocr_needed,
    "out_dir": str(out_dir),
}
(out_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))

print(f"分页完成: 共 {total} 页, 文字层 {len(text_layer)}, 需OCR {len(ocr_needed)}")
print(f"meta: {out_dir}/meta.json")
```

运行:
```
execute_shell('python3 /tmp/openclaw_workspace/pdf_extract.py input.pdf /tmp/openclaw_workspace/pdf_pages')
```

## Step 2: 并行 OCR (调用 vision_ocr)

**大文件批处理原则**: 一次 ≤ 5 页 OCR, 防视觉模型并发崩。

```
# 伪代码: 遍历 meta.ocr_needed, 分批
# 每批用 spawn_agent(parallel_tasks=[...])

batches = [ocr_needed[i:i+5] for i in range(0, len(ocr_needed), 5)]
for batch in batches:
    spawn_agent(parallel_tasks=[
        {"task": f"调 vision_ocr('/tmp/openclaw_workspace/pdf_pages/page_{p:03d}.png', prompt='请逐字识别页面内容, 保留标题/段落/表格/列表/公式结构, 输出 Markdown.'). 把返回结果 write_file 到 /tmp/openclaw_workspace/pdf_pages/page_{p:03d}.md",
         "agent_type": "researcher"}  # researcher 有 vision_ocr 权限
        for p in batch
    ])
```

> **注意**: writer 也能调 vision_ocr (v1.1 起), 但小文件用 researcher 更便宜。
> 总页数 < 10 时可以直接主 agent 串行调用 vision_ocr, 不用 spawn_agent。

## Step 3: 按页号顺序拼接

```python
# write_file: /tmp/openclaw_workspace/pdf_concat.py
import json, sys
from pathlib import Path

out_dir = Path(sys.argv[1])
out_path = sys.argv[2]
meta = json.loads((out_dir / "meta.json").read_text())

parts = [f"# {Path(sys.argv[3]).name} — OCR 结果\n"]
for i in range(1, meta["total"] + 1):
    parts.append(f"\n\n## Page {i}\n")
    if str(i) in meta["text_layer"]:
        parts.append(meta["text_layer"][str(i)])
    else:
        md_file = out_dir / f"page_{i:03d}.md"
        if md_file.exists():
            parts.append(md_file.read_text())
        else:
            parts.append(f"[Page {i} OCR 失败, 原图: pdf_pages/page_{i:03d}.png]")

Path(out_path).write_text("\n".join(parts))
print(f"合并完成: {out_path} ({Path(out_path).stat().st_size} bytes)")
```

运行:
```
execute_shell('python3 /tmp/openclaw_workspace/pdf_concat.py /tmp/openclaw_workspace/pdf_pages /tmp/openclaw_workspace/out.md input.pdf')
```

## 大文件优化 (30MB / 200+ 页)

### (1) 分片抽取
不要一次 pdfplumber.open 整份, 每次只处理 50 页, 减少内存:
```python
from pdfplumber import open as pdfopen
for start in range(0, total, 50):
    with pdfopen(pdf_path, pages=list(range(start+1, min(start+51, total+1)))) as pdf:
        ...
```

### (2) 分片 OCR
总页数 > 50 时用 DAG 代替 parallel_tasks, 错一批不崩全局:
```
spawn_agent(dag_tasks=[
    {"step_id": "batch_1",  "agent_type": "researcher", "task": "OCR page 1-50", ...},
    {"step_id": "batch_2",  "agent_type": "researcher", "task": "OCR page 51-100", "depends_on": []},
    ...
    {"step_id": "merge", "agent_type": "coder", "task": "拼接", "depends_on": ["batch_1","batch_2",...]}
])
```

### (3) 缓存
每页 OCR 完立刻 write_file, 中途崩了下次可续跑。合并前检查 page_NNN.md 是否已存在, 存在则跳过。

## 质量校验 (必做)

写完 out.md 后:
```bash
wc -m /tmp/openclaw_workspace/out.md  # 字符数
# 抽样: head -200, tail -200, grep 100 个随机页号
python3 -c "
import re
t = open('/tmp/openclaw_workspace/out.md').read()
pages = len(re.findall(r'^## Page \d+$', t, re.M))
failed = t.count('[Page') - pages  # [Page N OCR 失败] 算一次失败
print(f'已收录 {pages} 页, 失败 {failed} 页')
"
```

## 禁止

- ❌ 不检测文字层就全部走 OCR (浪费视觉模型 token)
- ❌ 30 页以下用 DAG (增加复杂度, 主 agent 直接串行更好)
- ❌ 并发 > 5 个 vision_ocr (视觉模型容易超时)
- ❌ 合并时忘记按页号排序 (必须 1..N 顺序)
- ❌ 不清理 pdf_pages/ 临时目录 (大 PDF 几百 MB 图片)

## Docx / Xlsx / PPTX 文档

本 skill 专注 PDF。其他格式走:
- `.docx` → load_skill docx (python-docx, 不需要 OCR, 直接拿文字)
- `.xlsx` → load_skill xlsx (openpyxl + pandas)
- `.pptx` → load_skill pptx (python-pptx)
- `.jpg/.png` → 直接调 `vision_ocr(path)`

## 触发示例

用户: "把这份 30MB PDF 论文转成 md"

→ 执行:
1. execute_shell: `ls -la input.pdf && python3 -c "import pdfplumber; p=pdfplumber.open('input.pdf'); print(f'pages={len(p.pages)}')"` 探查
2. write_file: pdf_extract.py (Step 1)
3. execute_shell: 运行 pdf_extract.py
4. 根据 meta.json.ocr_pages:
   - 0 页: 跳到 Step 3 直接合并
   - 1-10 页: 主 agent 串行调 vision_ocr
   - 11+ 页: spawn_agent(parallel_tasks) 分批 5 页 OCR
5. write_file: pdf_concat.py (Step 3)
6. execute_shell: 运行 pdf_concat.py
7. wc + head + tail 验证
