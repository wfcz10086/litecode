---
name: pdf-ocr-pipeline
description: >
  把任意大小的 PDF (尤其是带图片/扫描件/混合版式) 提取为按页面顺序拼接的 Markdown.
  自动检测每页有没有文字层: 有 → pdfplumber 直接抽取; 没有 → 栅格化成 PNG 后 vision_ocr 识别
  (paths 多页一批, 每批 ≤8 张). 最后按页号顺序合并成单一 out.md. 支持 30MB / 200+ 页大文件.
  触发词: pdf转md, pdf ocr, 扫描件识别, pdf提取, pdf to markdown, 论文pdf, 报告pdf.
---

# PDF → Markdown 有序拼接 Pipeline

处理 **任意 PDF → out.md**, 保持页面顺序, 文字 + 图片内容合二为一。

## 适用场景

- ✅ 纯文字 PDF (论文 / 小说 / 报告)
- ✅ 纯扫描件 PDF (图片化的古籍 / 合同扫描)
- ✅ 混合 PDF (有文字层 + 有嵌入图片/图表)
- ✅ 大文件 (30MB / 200+ 页, 分批 + 断点续跑)
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
┌─ 2. 分批 OCR (vision_ocr paths) ──────────────┐
│   每批 1~8 页 (按文字密度), 一次调用           │
│   vision_ocr(paths=[page_012.png, ...])        │
│   按 <<<PAGE N>>> 切开写 page_NNN.md           │
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
# write_file: /tmp/litecode_workspace/pdf_extract.py
import pdfplumber
import pypdfium2 as pdfium
from pathlib import Path
import sys, json, os

pdf_path = sys.argv[1]
out_dir = Path(sys.argv[2])  # e.g. /tmp/litecode_workspace/pdf_pages
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
execute_shell('python3 /tmp/litecode_workspace/pdf_extract.py input.pdf /tmp/litecode_workspace/pdf_pages')
```

## Step 2: OCR (vision_ocr, 多页一批)

`vision_ocr` 支持 `paths` 一次传多张图 (**上限 8 张**), 主 agent 直接分批调用即可, 一般不需要派子代理。

**批大小按文字密度定** (一次调用的所有图共用 `max_tokens`, 塞多了后几页会被截断):
| 页面类型 | 每批 | max_tokens |
| --- | --- | --- |
| 幻灯片、图片为主、字少 | 6~8 页 | 8192 |
| 普通正文页 | 2~3 页 | 8192 |
| 密集小字、表格、古籍 | 1 页 | 8192 |

```
# 每批调用 (以 3 页为例; 页号来自 meta.ocr_needed)
vision_ocr(
  paths=["/tmp/litecode_workspace/pdf_pages/page_012.png",
         "/tmp/litecode_workspace/pdf_pages/page_013.png",
         "/tmp/litecode_workspace/pdf_pages/page_014.png"],
  prompt="这 3 张图依次是 PDF 第 12、13、14 页。逐字识别每页内容, 保留标题/段落/表格/列表/公式结构, 输出 Markdown。"
         "每页必须以单独一行 `<<<PAGE 12>>>` 这样的标记开头 (用真实页号), 不要省略、不要总结。",
  max_tokens=8192)
```
拿到结果后:
1. 按 `<<<PAGE N>>>` 切开, 每页写到 `pdf_pages/page_NNN.md` (用 execute_shell 跑一小段 Python 切分写盘, 别手抄)。
2. **数标记**: 切出来的页数 ≠ 本批图数, 或某页内容明显被截断 → 把缺的页单独 (1 页 1 批) 重跑。
3. 只想要纯文字不要画面描述: 不写 prompt 时用 `mode="ocr"`; 写了 prompt 就以 prompt 为准 (prompt 会覆盖 mode)。

主模型不支持视觉也没关系, vision_ocr 会自动转给 `vision_fallback_id` 配置的视觉模型。

**要不要派子代理**: 需要 OCR 的页 ≤ 40 页, 主 agent 串行分批即可。更多页时可以用 `spawn_agent(parallel_tasks=[...])` 按页段拆给 **writer** 子代理 (子代理里只有 writer 带 vision_ocr; researcher 没有), 每个子代理负责一个页段并把 page_NNN.md 写盘, **同时运行的不超过 3 个**, 防视觉模型排队超时。

## Step 3: 按页号顺序拼接

```python
# write_file: /tmp/litecode_workspace/pdf_concat.py
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
execute_shell('python3 /tmp/litecode_workspace/pdf_concat.py /tmp/litecode_workspace/pdf_pages /tmp/litecode_workspace/out.md input.pdf')
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
需要 OCR 的页 > 40 时按页段拆给子代理 (agent_type 用 **writer**, 只有它带 vision_ocr), 每段 30~50 页, 同时最多 3 个:
```
spawn_agent(parallel_tasks=[
    {"agent_type": "writer", "task": "对 /tmp/litecode_workspace/pdf_pages/ 下 page_001~050.png 按 pdf-ocr-pipeline 的 Step 2 分批调 vision_ocr(paths=...), 每页结果写 page_NNN.md; 已存在的 page_NNN.md 跳过"},
    {"agent_type": "writer", "task": "同上, 负责 page_051~100.png"},
])
```
子代理结论带 [未验证] 时, 合并前自己数一遍 page_NNN.md 是否齐全。

### (3) 缓存
每页 OCR 完立刻 write_file, 中途崩了下次可续跑。合并前检查 page_NNN.md 是否已存在, 存在则跳过。

## 质量校验 (必做)

写完 out.md 后:
```bash
wc -m /tmp/litecode_workspace/out.md  # 字符数
# 抽样: head -200, tail -200, grep 100 个随机页号
python3 -c "
import re
t = open('/tmp/litecode_workspace/out.md').read()
pages = len(re.findall(r'^## Page \d+$', t, re.M))
failed = t.count('OCR 失败')
print(f'已收录 {pages} 页, 失败 {failed} 页')
"
```

## 禁止

- ❌ 不检测文字层就全部走 OCR (浪费视觉模型 token)
- ❌ 需要 OCR 的页不多 (≤40) 还派子代理 (主 agent 用 paths 分批更快)
- ❌ 同时 > 3 个子代理并发调 vision_ocr (视觉模型排队超时)
- ❌ 文字密集页一批塞 8 张 (共用 max_tokens, 后几页会被截断)
- ❌ 派 researcher 子代理做 OCR (它没有 vision_ocr)
- ❌ 合并时忘记按页号排序 (必须 1..N 顺序)
- ❌ 不清理 pdf_pages/ 临时目录 (大 PDF 几百 MB 图片)

## Docx / Xlsx / PPTX 文档

本 skill 专注 PDF。其他格式走:
- `.docx` → load_skill docx (python-docx, 不需要 OCR, 直接拿文字)
- `.xlsx` → load_skill xlsx (openpyxl + pandas)
- `.pptx` → load_skill pptx (python-pptx)
- `.jpg/.png` → load_skill image-ocr (vision_ocr, 多图对比用 paths)

## 触发示例

用户: "把这份 30MB PDF 论文转成 md"

→ 执行:
1. execute_shell: `ls -la input.pdf && python3 -c "import pdfplumber; p=pdfplumber.open('input.pdf'); print(f'pages={len(p.pages)}')"` 探查
2. write_file: pdf_extract.py (Step 1)
3. execute_shell: 运行 pdf_extract.py
4. 根据 meta.json.ocr_pages:
   - 0 页: 跳到 Step 3 直接合并
   - 1-40 页: 主 agent 按 Step 2 分批调 vision_ocr(paths=...)
   - 40+ 页: spawn_agent(parallel_tasks) 按页段拆给 writer 子代理
5. write_file: pdf_concat.py (Step 3)
6. execute_shell: 运行 pdf_concat.py
7. wc + head + tail 验证
