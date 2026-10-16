"""LiteCode 版本号 + 版本绰号: 唯一来源。

/health、/api/health、网页顶栏、启动日志、命令行横幅都从这里读。
发版只改这里, 并在 README 的「版本更新」里补一段。
"""
VERSION = "v1.10.16.88"
CODENAME = "憨包"
CODENAME_EN = "Hanbao"


def label(lang: str = "zh") -> str:
    return f"{VERSION} 「{CODENAME}」" if lang == "zh" else f'{VERSION} "{CODENAME_EN}"'


def info() -> dict:
    return {"version": VERSION, "codename": CODENAME, "codename_en": CODENAME_EN}


if __name__ == "__main__":   # entrypoint.sh 打印启动横幅用
    print(f"LiteCode {label()} · {CODENAME_EN}")
