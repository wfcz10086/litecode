---
name: long-translate
description: >
  整本书/百万字级长文翻译: 小说、技术书、长报告、字幕。txt / md / docx / epub / pdf(文字层) / srt 输入,
  输出保持原格式 (docx 段落样式与加粗、md 标题/代码块/链接、epub 逐章含行内标签、srt 时间轴)。
  全书扫描生成可编辑术语表 → 按章节切块并发翻译 → 断点续传 → 术语一致性/漏译检查 → 质检报告。
  后台长跑, 随时看进度, 中断后重跑同一命令即续跑。
aliases: [翻译整本书, 翻译小说, 翻译全书, 长文翻译, 百万字翻译, 翻译电子书, 翻译epub, 翻译字幕, 书籍翻译, translate book, translate novel]
---

# 长文翻译 (百万字级, 断点续传)

几千字的短文直接翻就行; **超过约 1 万字、或要保持 docx/epub/md 格式、或要全书术语统一** 用本技能。
一切都在脚本里跑 (模型调用、切块、合并), 正文不进对话上下文。

```bash
D=${LITECODE_HOME:-/opt/litecode}/skills/long-translate/scripts
W=/tmp/litecode_workspace/translate/<书名>        # 工作目录: 切块、术语表、每块译文、日志、成品都在这
```
**`D=` 这一行原样写进每条 execute_shell 命令**, 让 shell 展开, 别自己替换路径。

## 1. 启动 (后台)

```bash
D=${LITECODE_HOME:-/opt/litecode}/skills/long-translate/scripts; W=/tmp/litecode_workspace/translate/<书名>
mkdir -p $W && nohup python3 $D/long_translate.py run <原文路径> -w $W --to zh -j 4 >> $W/run.log 2>&1 &
echo started
```
- `run` = init (抽段+切块) → glossary (全书扫描术语+摘要) → translate (并发) → build (成品+质检报告)。
- 常用参数: `--to zh|en|ja…` 目标语言; `--from` 源语言 (默认自动); `-j` 并发 (默认 4, 上限 8; 自建模型别超过 4);
  `--style "古典小说腔"` / `"技术文档, 专有名词首次出现括注英文"`; `--model <config 里的模型 id>` (默认当前激活模型);
  `--chunk-tokens` 每块原文 token (默认 1800, 一般别动); `--fix-terms` 翻完自动重翻术语不一致的块。
- 模型配置读 LiteCode 的 config.json (当前激活模型的 backend_url / api_key / served_model_name), 不需要另配。
- 默认发 `reasoning_effort=low`: 翻译不需要长思考。GLM-5.3-Flash 实测默认思考会让输出 token 多 ~20 倍、
  慢 ~5 倍且容易被截断。上游不认这个参数会自动去掉; 想用模型默认思考加 `--thinking auto`。

**先让用户确认术语表 (推荐, 长篇小说尤其)**: 只跑前两步, 把术语表给用户看, 改完再翻:
```bash
python3 $D/long_translate.py init <原文> -w $W --to zh
python3 $D/long_translate.py glossary $W -j 4           # → $W/glossary.tsv
```
`glossary.tsv` 每行 `原文<TAB>译文<TAB>类型<TAB>次数<TAB>备注`。备注里有「候选: A×3 / B×1」说明各窗口译法有分歧,
「已统一 X→Y」说明脚本已按多数票统一了人名姓氏。用户要改就直接编辑译文列 (用 edit_file, 保留 TAB);
删掉一行 = 不约束该词。然后用上面的 `run` 命令接着跑 (已有的不会重做)。

## 2. 看进度

```bash
python3 $D/long_translate.py status $W        # 块进度、是否在跑、累计 token、按当前速度的剩余时间、日志末 5 行
tail -n 3 $W/run.log                           # 每完成一块一行: 进度% / 用时 / 剩余≈ / token / 失败数
```
给用户汇报: 进度百分比、预计剩余时间、累计 token。**不要 sleep 轮询等它跑完**, 汇报完就结束本轮; 用户问再查。

## 3. 中断与续跑

- 每块译文完成即原子写入 `$W/out/chunk_xxxxx.json`, kill -9、容器重启、断网都不会损坏已完成部分。
- 续跑 = **原样再执行一次第 1 步的 run 命令**。已完成块跳过, 失败块重试, 扫描中断的窗口补扫。
- 原文文件变了 (sha 不同) 会拒绝混用旧进度; 确实要重来加 `--reset`。
- 要停: `kill $(cat $W/run.pid)` (SIGTERM: 等进行中的块写完再退)。

## 4. 质量

翻译中自动做:
- 每块按 `⟪n⟫` 段标记对齐, **漏段 / 译文明显过短 (疑似漏译或截断) / 输出被截断** → 带反馈重翻一次,
  仍不行就把块对半拆开重翻; 最终仍有问题的写进告警。
- 每块只注入本块命中的术语; 附前情摘要 (扫描阶段产出) + 上一块原文/译文末尾, 保证衔接和称谓一致。

翻完 (build 会自动出 `$W/qa_report.md`):
```bash
python3 $D/long_translate.py check $W                 # 术语不一致 / 漏译段 / 原文残留 统计
python3 $D/long_translate.py check $W --fix-terms     # 把有问题的块带反馈重翻 (只重翻问题块)
python3 $D/long_translate.py translate $W --redo 12,30-32   # 指定块重翻
python3 $D/long_translate.py review $W --sample 5     # 可选: 随机抽 5 块让模型对照原文打分 (5 分制) 并列问题
python3 $D/long_translate.py polish $W -j 4           # 可选: 第二遍润色 (成本≈再翻一遍), 结果可疑的段保留初译
python3 $D/long_translate.py build $W                 # 重新生成成品 + 质检报告 (改过任何东西后都要 build)
```
质检报告里「未出现标准译名」多是代词指代 (Elizabeth → 她), 只供抽查, 不用全部处理;
「明确用了别的译名」「漏译段」「原文残留」才是要修的。

## 5. 交付

成品在 `$W/<原名>.<目标语言>.<扩展名>` (pdf 输入输出为 .md), 报告 `$W/qa_report.md`。
回复里给: 成品路径、报告路径、字数、耗时、token、质检结论 (问题数 + 是否已修)。不要把译文正文贴进回复。

## 规模参考 (GLM-5.3-Flash 自建 8×5090, 并发 4, 实测《傲慢与偏见》前 14 章 2.09 万词)

| 项 | 实测 (2.09 万英文词 → 3.1 万汉字) | 推算 100 万汉字译文 (≈67 万英文词) |
|---|---|---|
| 术语扫描 | 6 次调用, 入 2.8 万 / 出 0.4 万 token, 9 秒 | 入 ≈90 万 / 出 ≈15 万, ≈5 分钟 |
| 翻译 (一遍) | 23 次调用, 入 4.8 万 / 出 2.9 万 token, 56 秒 | 入 ≈150 万 / 出 ≈95 万, ≈30 分钟 |
| 术语修正重翻 | 约 +50%~80% 的块 (第一遍名称漂移时) | 视术语表质量, +0~25 分钟 |

润色 / 抽检另算 (润色≈再翻一遍)。共用 GPU 时速度会降; 不要为了提速把并发开到 8 以上。

## 已知限制

- docx: 段落样式、表格、页眉页脚保留; 段内按格式分组 (加粗/斜体/链接) 回写, 模型调换了分组顺序时退回整段写入首个 run。
  脚注/批注/文本框/SmartArt 不翻。制表符和段内换行会丢。
- epub: 行内标签用占位符保留, 模型弄坏时退回纯文本 (丢该段行内格式)。目录 (nav/ncx) 的标题不翻。
- pdf: 只取文字层并重排段落, 输出 Markdown, 不还原版式; 扫描件先用 pdf-ocr-pipeline 出文本。
- txt: 硬换行段落会合并成一行 (短行如诗歌保留换行)。
- 字幕: 按条翻译, 跨条的长句可能语序别扭; 不会重新切分时间轴。
- 公版名著模型常复现已出版译本的译名 (如王科一译《傲慢与偏见》的"彬格莱"); 以术语表为准, 用 check --fix-terms 统一。
