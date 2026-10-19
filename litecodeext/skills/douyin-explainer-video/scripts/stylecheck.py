#!/usr/bin/env python3
"""文案闸: 风格 + 深度 + 废话。用法 (项目目录): python3 stylecheck.py [script.json]   有错 → 非 0 退出

来历 (2026-10-19): 「2027 经济预测」成片 7 场每场念一个数, 没一句"为什么", 结尾把"跟你钱包最近的三条"
推到下期 —— 用户原话"没啥深度"。这道闸只拦机械能查出来的毛病, 写法见 copywriting.md。

规则:
  1. 必须写 "style" (copywriting.md 的风格名), 不同题材用不同写法
  2. 分析类风格: 至少一半的场景里有因果/判断词 (因为/所以/意味着/代价/谁买单…), 否则就是在念数据表
     故事类风格 (lezige 乐子哥 / beiju 克制悲剧) 靠反差推进, 不查这条
  3. 一场里 ≥3 个数字却一个因果词都没有 → 提示 (数字罗列)
  4. 废话词直接拦: 让我们拭目以待 / 值得关注 / 各有利弊 …
  5. 价值不外包: 最后一场不许把"最重要/最关键/跟你钱包有关"的东西推到下期
"""
import json
import re
import sys

STYLES = {
    "chaizhang": "拆账 · 财经经济政策行业",
    "benzhi": "本质拆解 · 社会议题",
    "fangongshi": "反共识 · 科技 AI 互联网",
    "fanan": "翻案 · 历史",
    "jiemi": "悬念揭秘 · 冷知识科学",
    "bikeng": "揭底避坑 · 消费生活",
    "lezige": "乐子哥冷嘲 · 人物 国际时政",
    "beiju": "克制悲剧 · 人物陨落",
}
STORY = {"lezige", "beiju"}
CAUSAL = re.compile(r"因为|所以|于是|导致|意味着|说白了|本质|代价|结果|凭什么|为什么|靠什么|靠的是|谁在|谁买单|谁赚|"
                    r"换句话说|也就是说|一旦|只要|才会|反而|恰恰|真相|其实|问题是|关键是|说明|等于|不是.{1,12}而是|不是.{1,12}是")
FLUFF = ("让我们拭目以待", "拭目以待", "值得关注", "值得我们关注", "各有利弊", "见仁见智", "不得不说", "众所周知",
         "一起来看看", "未来可期", "引发热议", "令人深思", "发人深省", "有人认为", "仁者见仁")
DEFER = re.compile(r"下期.{0,14}(最重要|最关键|核心|真正|钱包|最近的|干货|答案)")
NUM = re.compile(r"\d+(?:\.\d+)?|[一二三四五六七八九十两]+(?:点[一二三四五六七八九零]+)?(?:万亿|亿|万|倍|成)|百分之[一二三四五六七八九十零点]+|(?<!百分之)[一二三四五六七八九十两]+点[一二三四五六七八九零]+")


def check(sj):
    """→ (错误列表, 提示列表)"""
    errs, tips = [], []
    scenes = sj.get("scenes", [])
    style = sj.get("style")
    if style not in STYLES:
        errs.append(f"script.json 要写 \"style\" (收到 {style!r}), 可选: "
                    + " / ".join(f"{k} ({v})" for k, v in STYLES.items()) + " —— 写法见 copywriting.md")
    for s in scenes:
        t = s.get("text", "")
        for w in FLUFF:
            if w in t:
                errs.append(f"{s.get('id')}: 废话「{w}」—— 换成一句有立场的判断")
                break
        if len(NUM.findall(t)) >= 3 and not CAUSAL.search(t):
            tips.append(f"{s.get('id')}: 一场 {len(NUM.findall(t))} 个数字却没有因果/判断, 像在念表 —— 留一个数, 讲清它为什么、意味着什么")
    if style in STYLES and style not in STORY and scenes:
        deep = [s.get("id") for s in scenes if CAUSAL.search(s.get("text", ""))]
        need = (len(scenes) + 1) // 2
        if len(deep) < need:
            shallow = [s.get("id") for s in scenes if s.get("id") not in deep]
            errs.append(f"深度不够: 只有 {len(deep)}/{len(scenes)} 场讲了为什么/所以呢 (至少 {need} 场)。"
                        f"这几场只在报数: {shallow} —— 每场回答一个'为什么'或'对谁意味着什么'")
    if scenes:
        last = scenes[-1].get("text", "")
        m = DEFER.search(last)
        if m:
            errs.append(f"{scenes[-1].get('id')}: 把最有价值的部分推到下期「{m.group(0)}」"
                        "—— 本期结论本期讲完, 下期预告只做钩子")
    return errs, tips


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "script.json"
    sj = json.load(open(src, encoding="utf-8"))
    errs, tips = check(sj)
    for t in tips:
        print("  ⚠", t)
    for e in errs:
        print("  ✗", e)
    print(f"文案闸: {'通过' if not errs else f'{len(errs)} 处问题'} (style={sj.get('style')})")
    sys.exit(1 if errs else 0)


if __name__ == "__main__":
    main()
