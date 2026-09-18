#!/usr/bin/env python3
"""易经复刻 v1 / v2 对照 — 量化基础设施与提示词修复带来的差异。

用法:
    compare_yijing_versions.py <v1目录> <v2目录>

关键观测点是**卦辞内容正确性**: v1 拿自建参考文件自证, 63/64 "通过",
而第 21 卦噬嗑的卦辞抄成了第 33 卦遁的 —— 参考文件里也错, 所以对得上。
这里用独立写出的通行本标准比对, 不依赖任何一版自己的参考文件。
"""
import re
import subprocess
import sys
from pathlib import Path

# 通行本 (王弼/朱熹本) 六十四卦短名, 按卦序 —— 独立于两个被测版本。
# 查短名而非全称: 两版都把全称放在 init() 里由上下卦推导, 源码里只有短名字面量。
STD_SHORT = """乾 坤 屯 蒙 需 讼 师 比 小畜 履 泰 否 同人 大有 谦 豫
随 蛊 临 观 噬嗑 贲 剥 复 无妄 大畜 颐 大过 坎 离 咸 恒
遁 大壮 晋 明夷 家人 睽 蹇 解 损 益 夬 姤 萃 升 困 井
革 鼎 震 艮 渐 归妹 丰 旅 巽 兑 涣 节 中孚 小过 既济 未济""".split()

# 抽样卦辞 (通行本原文), 用于内容正确性核对
STD_GUACI = {
    "乾":   "元亨，利贞。",
    "坤":   "元亨，利牝马之贞。",          # 前缀比对
    "噬嗑": "亨。利用狱。",                 # ← v1 在这里抄成了遁卦的
    "遁":   "亨，小利贞。",
    "大有": "元亨。",
    "谦":   "亨，君子有终。",
    "既济": "亨小，利贞。",
    "未济": "亨。小狐汔济",                 # 前缀比对
}


def scan(root: Path) -> dict:
    gos = [p for p in root.rglob("*.go") if ".git" not in p.parts]
    src = [p for p in gos if not p.name.endswith("_test.go")]
    tst = [p for p in gos if p.name.endswith("_test.go")]

    def lines(ps):
        n = 0
        for p in ps:
            try:
                n += len(p.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                pass
        return n

    testfns = 0
    for p in tst:
        testfns += len(re.findall(r"^func Test", p.read_text(encoding="utf-8", errors="replace"), re.M))

    blob = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in src)

    # 卦名覆盖
    missing = [n for n in STD_SHORT if f'"{n}"' not in blob]
    # 抽样卦辞
    guaci_bad = []
    for name, expect in STD_GUACI.items():
        # 在同一行里找 "<卦名>" ... "<卦辞前缀>"
        hit = False
        for ln in blob.splitlines():
            if f'"{name}"' in ln and expect[:6] in ln:
                hit = True
                break
        if not hit:
            guaci_bad.append(name)

    return {
        "files":     len([p for p in root.rglob("*") if p.is_file() and ".git" not in p.parts]),
        "go_src":    len(src),
        "go_test":   len(tst),
        "src_lines": lines(src),
        "tst_lines": lines(tst),
        "test_fns":  testfns,
        "name_miss": missing,
        "guaci_bad": guaci_bad,
        "has_readme": (root / "README.md").exists(),
        "has_plan":   (root / "PLAN.md").exists(),
    }


def build_and_test(root: Path) -> tuple[str, str]:
    # go 装在容器里, 宿主没有 —— 把目录送进去跑
    def run(cmd: str):
        try:
            tar = subprocess.run(["tar", "cf", "-", "-C", str(root.parent), root.name],
                                 capture_output=True, timeout=120)
            subprocess.run(["docker", "exec", "-i", "litecode", "bash", "-c",
                            f"rm -rf /tmp/_cmp && mkdir -p /tmp/_cmp && tar xf - -C /tmp/_cmp"],
                           input=tar.stdout, capture_output=True, timeout=120)
            r = subprocess.run(["docker", "exec", "litecode", "bash", "-c",
                                f"cd /tmp/_cmp/{root.name} && {cmd} 2>&1"],
                               capture_output=True, text=True, timeout=600)
            out = r.stdout.strip()
            return "OK" if r.returncode == 0 and "FAIL" not in out else out[:200]
        except Exception as exc:
            return f"跑不了: {exc}"
    return run("go build ./..."), run("go test ./... | grep -vE '^2026/' | tail -6")


def main() -> int:
    v1, v2 = Path(sys.argv[1]), Path(sys.argv[2])
    a, b = scan(v1), scan(v2)
    rows = [
        ("文件数",        a["files"],      b["files"]),
        ("Go 源文件",     a["go_src"],     b["go_src"]),
        ("Go 测试文件",   a["go_test"],    b["go_test"]),
        ("源码行数",      a["src_lines"],  b["src_lines"]),
        ("测试行数",      a["tst_lines"],  b["tst_lines"]),
        ("测试函数",      a["test_fns"],   b["test_fns"]),
        ("测试/源码比",   f'{a["tst_lines"]/max(a["src_lines"],1):.2f}',
                          f'{b["tst_lines"]/max(b["src_lines"],1):.2f}'),
        ("README",        "有" if a["has_readme"] else "无", "有" if b["has_readme"] else "无"),
        ("PLAN.md",       "有" if a["has_plan"] else "无",   "有" if b["has_plan"] else "无"),
        ("缺失卦名",      len(a["name_miss"]), len(b["name_miss"])),
        ("抽样卦辞错",    len(a["guaci_bad"]), len(b["guaci_bad"])),
    ]
    w = max(len(r[0]) for r in rows) + 2
    print(f"{'':<{w}}{'v1':>12}{'v2':>12}")
    print("─" * (w + 24))
    for name, x, y in rows:
        print(f"{name:<{w}}{str(x):>12}{str(y):>12}")
    for tag, d in (("v1", a), ("v2", b)):
        if d["name_miss"]:
            print(f"\n{tag} 缺失卦名: {d['name_miss']}")
        if d["guaci_bad"]:
            print(f"{tag} 卦辞对不上通行本: {d['guaci_bad']}")
    print()
    for tag, root in (("v1", v1), ("v2", v2)):
        bd, ts = build_and_test(root)
        print(f"{tag}  build={bd[:40]}  test={ts[:60]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
