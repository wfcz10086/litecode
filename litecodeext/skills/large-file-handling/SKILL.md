---
name: large-file-handling
description: >
  大文件 (几十 MB ~ GB 级) 全链路处理规范: 先量大小再选工具; read_file 分段续读、
  大日志先搜后读、大 CSV/Excel/JSON 用脚本流式统计不进上下文; Web 上传上限与 413、
  下载断点 (Range)、ssh_write_file(local_path) 断点续传 + sha256 校验、Windows 节点分块传输。
  触发词: 大文件、超大日志、几百MB、1GB、文件太大、读不完、上传失败 413、断点续传、
  传到服务器、sha256 校验、大 CSV、大 Excel、百万行、日志分析很慢。
---

# 大文件处理

核心原则: **上下文只放结论和定位到的片段, 不放原始数据。** 1MB 文本 ≈ 30 万 token,
任何"先读进来再说"的做法都会撑爆上下文或被截断后瞎猜。

## 第 0 步: 先量再动手 (每次都做)

```bash
ls -lh <file>; wc -l <file>; file <file>      # 大小 / 行数 / 类型 (gz/zip/二进制?)
head -c 2000 <file>                            # 看格式, 别 cat 全文
```

| 规模 | 文本/代码 | 日志 | CSV/Excel/JSON |
|---|---|---|---|
| < 2000 行 | read_file 直接读 | tail_log / read_file | read_file 看结构即可 |
| 2000 行 ~ 50MB | read_file 分段 + search_code 定位 | 先搜后读 | python 脚本 |
| > 50MB | 只用搜索/脚本定位, 按行号读附近 | grep/awk 统计 + tail_log | pandas 分块 / duckdb |

## 读: read_file 分段协议

- 任何大小都能读。长文件返回头部会写 `共 N 行 / 本次第 a-b 行 / 后面还剩 R 行`,
  末尾给出下一段的准确参数 `lines='b+1:...'`, 照抄即可续读。
- 单次最多 500 行 / 20000 字符; 超长单行 (压缩 JSON / minified JS) 只显示前 2000 字节并标注原长。
- `lines='-100:'` 读最后 100 行; `lines='5000:'` 从 5000 行读到上限。
- **不要逐段通读几万行**: 先定位 (search_code / `grep -n`), 再 `lines='命中行-30:命中行+30'`。
- 二进制扩展名 (xlsx/pdf/docx/zip…) read_file 会拒绝并给出该用的技能/脚本。

## 日志

```bash
grep -c ERROR app.log                                   # 先看量级
grep -n -m 20 -E 'ERROR|Traceback|FATAL' app.log        # 前 20 个命中带行号
awk '$3=="ERROR"{c[$4]++} END{for(k in c) print c[k],k}' app.log | sort -rn | head   # 按模块聚合
sed -n '/12:00:00/,/12:05:00/p' app.log | head -200    # 时间窗
zcat app.log.1.gz | grep -n ... | head                  # 轮转压缩日志不用解压落盘
```
- `tail_log(path, lines=50)`: 带总行数和"往前翻"的 read_file 参数, 输出 ≤30000 字符;
  `follow_seconds=10` 观察实时输出。
- search_code 对大文件是逐行流式扫描, 20s 封顶; GB 级目录直接 `execute_shell` 跑 `rg -n`/`grep -n`。
- 远端日志就地分析: `ssh_exec` 在远端跑 grep/awk/python, 只把统计结果带回, 别把日志拉回来读。

## CSV / Excel / JSON: 写脚本, 只打印结果

```python
import pandas as pd
df = pd.read_csv(p, nrows=5); print(df.dtypes)                 # 先看结构
agg = None
for ch in pd.read_csv(p, chunksize=200_000, usecols=["day", "amount"]):   # 分块, 内存恒定
    g = ch.groupby("day")["amount"].sum()
    agg = g if agg is None else agg.add(g, fill_value=0)
agg.to_csv("/tmp/litecode_workspace/out/daily.csv"); print(agg.describe())
```
- Excel: `pd.read_excel(p, nrows=20)` 看表头; 大表 `openpyxl.load_workbook(p, read_only=True)` 逐行迭代,
  或先转 CSV 再分块。
- 装了 duckdb 时: `duckdb.sql("select day,sum(amount) from 'big.csv' group by 1")` 最快且不占内存。
- 大 JSON: `jq` 取字段; 顶层是数组用 `ijson` 流式解析; JSONL 逐行处理。
- 结果大就写文件 (workspace/out/), 回复里只给摘要 + 路径。

## 长文档改写/翻译/摘要

走 `longtext-processing` 技能: 脚本切片落盘 → 子代理按切片文件路径处理 → 合并。

## 传输

**Web 上传** (`/api/upload/{sid}`): 流式落盘, 上限 `config.web_ui.max_upload_mb` (默认 1024MB,
`GET /api/upload_limits` 可查)。超限 413 且给出原因; 返回 `sha256` 可与本地 `sha256sum` 核对。
超过上限: 先压缩/分卷 (`split -b 500M`), 或 scp/rsync 直接放进工作区 `uploads/`。

**Web 下载** (`/api/workspace/download`, `/api/media`): 流式发送, 支持 Range 断点
(`curl -C - -o f URL` 可续传); `litecli ws download` 流式写 `.part`, 收全才改名。

**传到 Linux 服务器**: `ssh_write_file(path=远程路径, local_path=本机文件)` —
1MB 分块流式, 两端 sha256 一致才原子替换目标 (原文件备份为 .bak, 不会留下半截文件);
中断后原样重试会从远端 `<path>.lcpart` 断点续传。多个文件:
`ssh_sync_files(files={"/opt/app/a.py": "内容", "/opt/app/dist.tgz": {"local_path": "/tmp/.../dist.tgz"}})`。
不要把大文件/二进制塞进 `content`。

**传到 Windows 节点**: `windows_system(action='upload', source=uploads/内文件)` 单文件 ≤50MB,
4MB 分块 + 整体 sha256; 更大的先 `7z a -v40m` 或 `split -b 40M` 分卷, 逐卷上传后在节点端合并并核对 sha256。

**传完必须核对**: 工具已自动比对 sha256; 手工传的 (scp/rsync) 自己两端 `sha256sum` 对一遍再报告完成。

## 反模式 (见到就停)

- `cat big.log` / `read_file` 不带 lines 反复读同一个大文件 / 一段段翻几万行找错误
- 把 CSV 全部行读进上下文再"心算"统计; `print(df)` 打印整表
- 把文件内容 base64 后塞进工具参数传远程
- 上传 413 后反复重试同一个文件 (先看报错里的上限, 压缩或分卷)
- 只看到 "OK" 就说传完了, 没核对大小/sha256
