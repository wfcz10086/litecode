---
name: anti-ai-tell-audit
description: >
  自动审查长文输出是否有"AI 味". 统计中括号心理描写 / 套话命中 / 重复句式 / 空洞形容词密度,
  给出修改建议. 配合 critic 实现"不合格重写"闭环.
  触发词: ai味, ai味道, 去ai味, 套话检查, 文风审查, 文风优化, 自审.
---

# 反 AI 味自动审查 (写完自动跑)

## 用途
写完任何长文 (小说章节 / 深度报告 / 文案) 后, 运行本 skill 的审查脚本, 拿到:
- **得分** (0-100, 60+ 算过关)
- **具体问题清单** (哪句话 / 哪段 / 统计数据)
- **修改建议** (针对每种问题)

## 审查维度 (6 大类 25 小项)

### 1. 结构性 AI tell
- `【...】` 心理描写 (最重 AI tell) — 超过 2 次/章必改
- `**粗体:内容**` 在散文里 — 超过 3 次必改
- `---` 分隔符 — 散文里不该出现
- `| 表格 |` — 小说章节不该有
- `[THINK]` `【需求分析】` 等 prompt 残留

### 2. 套话黑名单 (通用)
```
总而言之 / 综上所述 / 值得一提的是 / 不得不说 / 必须指出 / 毫无疑问
越来越多 / 事实证明 / 众所周知 / 据悉 / 大致可以分为 / 不可忽视
随着 / 然而 / 然后 / 接下来 / 首先 / 其次 / 最后
```

### 3. 小说套话黑名单
```
瞳孔一缩 / 冷笑一声 / 不可置信 / 眼神变得冰冷 / 毛骨悚然 / 不寒而栗
心里咯噔一下 / 汗毛倒竖 / 冷意从脚底升起 / 空气仿佛凝固
暗道 / 心中暗骂 / 暗自思忖 (频繁用)
缓缓 + 动词 (缓缓打开 / 缓缓起身) — 超过 3 次必改
```

### 4. 句式重复
- 连续 3 段以 "他 X" / "她 X" 开头
- 连续 3 段第一句少于 10 字
- 连续 3 段都是长句 (> 50 字)
- 对话连续 5 句都是 "A 说" 无场景

### 5. 空洞形容词
```
非常 / 十分 / 特别 / 极其 / 格外 / 尤其
很 + 形容词 (很强 / 很厉害 / 很漂亮) 泛滥
似的 / 一般 / 般的 (比喻过多)
```

### 6. 结构性问题
- **开头不抓人**: 第一句就是场景介绍 (应该用冲突 / 悬念 / 动作)
- **结尾无钩子**: 结尾没有未解冲突 / 暗示 / 停顿
- **中段全对话**: 连续 500 字全是 "A说 B说" 没场景
- **段落长度均匀**: 段落全是 50 字, 没有节奏感 (应该长短交错)

## 审查脚本 (核心产物)

```python
# write_file: /tmp/openclaw_workspace/ai_tell_audit.py
"""
用法: python3 ai_tell_audit.py <text_file> [--report <md_out>]
     python3 ai_tell_audit.py chapter_01.md --report audit.md
"""
import re, sys, json
from collections import Counter
from pathlib import Path

def chinese_count(text: str) -> int:
    return len(re.findall(r'[\u4e00-\u9fff]', text))

# 1. 结构 AI tell
STRUCT = {
    "【...】": r'【[^】]+】',
    "粗体": r'\*\*[^*\n]+\*\*',
    "分隔符": r'^---\s*$',
    "markdown表格": r'^\s*\|.+\|',
    "prompt残留": r'\[(?:THINK|PLAN|需求|意图)[^\]]*\]',
}

# 2-5. 词级黑名单
CLICHES_COMMON = [
    '总而言之','综上所述','值得一提的是','不得不说','必须指出','毫无疑问',
    '越来越多','事实证明','众所周知','据悉','大致可以分为','不可忽视',
]
CLICHES_NOVEL = [
    '瞳孔一缩','冷笑一声','不可置信','眼神变得冰冷','毛骨悚然','不寒而栗',
    '心里咯噔','汗毛倒竖','冷意从脚底','空气仿佛凝固','缓缓打开','缓缓起身',
    '缓缓睁开','缓缓抬起',
]
HOLLOW = ['非常','十分','特别','极其','格外','尤其']
FREQUENT_START = ['然而','然后','接下来','首先','其次','最后','随着']

def audit(text: str, kind: str = "novel"):
    cn = chinese_count(text)
    if cn < 200:
        return {"error": f"文本太短 ({cn} 汉字), 至少需要 200 字"}

    issues = []

    # 1. 结构问题
    for name, pat in STRUCT.items():
        hits = re.findall(pat, text, flags=re.MULTILINE)
        if hits:
            threshold = {"【...】": 2, "粗体": 3, "分隔符": 0, "markdown表格": 0, "prompt残留": 0}[name]
            if len(hits) > threshold:
                issues.append({
                    "level": "high" if name in ("【...】","prompt残留") else "mid",
                    "type": f"结构:{name}",
                    "count": len(hits),
                    "threshold": threshold,
                    "samples": hits[:3],
                })

    # 2. 套话 (通用)
    for word in CLICHES_COMMON:
        c = text.count(word)
        if c > cn // 3000 + 1:  # 3000 字最多 1 次
            issues.append({"level": "mid", "type": "套话:通用", "word": word, "count": c})

    # 3. 套话 (小说)
    if kind == "novel":
        for word in CLICHES_NOVEL:
            c = text.count(word)
            if c > cn // 2000 + 1:  # 2000 字最多 1 次
                issues.append({"level": "mid", "type": "套话:小说", "word": word, "count": c})

    # 4. 空洞形容词密度
    hollow_total = sum(text.count(w) for w in HOLLOW)
    if hollow_total > cn // 200:  # 200 字最多 1 次
        issues.append({"level": "mid", "type": "空洞形容词过多", "count": hollow_total, "per_kcn": hollow_total * 1000 / cn})

    # 5. 句首连接词
    start_freq = 0
    for line in text.split('\n'):
        line = line.strip()
        for w in FREQUENT_START:
            if line.startswith(w):
                start_freq += 1
                break
    if start_freq > cn // 500:
        issues.append({"level": "mid", "type": "连接词句首过多", "count": start_freq})

    # 6. 段落结构
    paras = [p for p in text.split('\n\n') if p.strip()]
    if paras:
        lens = [len(p) for p in paras]
        avg = sum(lens) / len(lens)
        # 段落长度方差 (越小越单调)
        var = sum((l - avg) ** 2 for l in lens) / len(lens)
        std = var ** 0.5
        if std < avg * 0.3 and len(paras) > 8:
            issues.append({"level": "low", "type": "段落长度过均匀 (缺节奏)", "avg": round(avg, 1), "std": round(std, 1)})

    # 7. 段首重复
    first_chars = [p.strip()[:3] for p in paras if p.strip()]
    cnt = Counter(first_chars)
    for start, n in cnt.most_common(3):
        if n >= 4 and start:
            issues.append({"level": "mid", "type": "段首重复", "start": start, "count": n})

    # 8. 对话密度 (简陋检测)
    dialog_lines = len(re.findall(r'"[^"]{1,50}"', text)) + len(re.findall(r'"[^"]{1,50}"', text))
    if kind == "novel" and dialog_lines > len(paras) * 0.7:
        issues.append({"level": "low", "type": "对话密度过高 (缺动作/环境)", "dialog_ratio": round(dialog_lines / max(1, len(paras)), 2)})

    # 总分: 100 - 每个 high/mid/low 扣 15/8/3
    score = 100
    for i in issues:
        score -= {"high": 15, "mid": 8, "low": 3}[i["level"]]
    score = max(0, score)
    passed = score >= 60 and not any(i["level"] == "high" for i in issues)

    return {
        "score": score,
        "passed": passed,
        "word_count": cn,
        "issues": issues,
    }

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 ai_tell_audit.py <text_file> [--kind novel|report] [--report out.md]")
        sys.exit(1)
    text = Path(sys.argv[1]).read_text()
    kind = "novel"
    out_md = None
    for i, a in enumerate(sys.argv):
        if a == "--kind" and i + 1 < len(sys.argv):
            kind = sys.argv[i + 1]
        if a == "--report" and i + 1 < len(sys.argv):
            out_md = sys.argv[i + 1]

    r = audit(text, kind=kind)
    print(json.dumps(r, ensure_ascii=False, indent=2))

    if out_md:
        md = [f"# AI 味审查报告", f"", f"- 文件: {sys.argv[1]}", f"- 字数: {r['word_count']}",
              f"- 得分: {r['score']}/100", f"- 结论: {'✅ 通过' if r['passed'] else '❌ 不合格, 需重写'}", ""]
        if r.get("issues"):
            md.append("## 问题清单\n")
            for i in r["issues"]:
                md.append(f"- **[{i['level']}] {i['type']}**: {json.dumps({k:v for k,v in i.items() if k not in ('level','type')}, ensure_ascii=False)}")
        Path(out_md).write_text("\n".join(md))
        print(f"\n报告: {out_md}")

    sys.exit(0 if r["passed"] else 1)
```

## 如何触发
```
# 小说章节
python3 ai_tell_audit.py chapter_01.md --kind novel --report audit_01.md

# 报告
python3 ai_tell_audit.py report.md --kind report --report audit_report.md
```

退出码 0 = 通过, 1 = 需重写. writer subagent 可据此决定是否回炉。

## 接入 critic 回炉环
```
writer 写完 chapter_N.md →
  execute_shell('python3 ai_tell_audit.py chapter_N.md --report audit_N.md')
  if exit != 0 → read audit_N.md 看问题清单 → 重写本章 (max 2 次)
  仍不过 → 上报主 agent 请用户决定
```
