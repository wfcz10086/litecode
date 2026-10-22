"""memory/facts.py — 从一句话里抽"用户档案事实": (key, value, 展示文本)。

用途:
  - save_memory 没给 scope 时判断该不该进全局档案 (偏好/身份类 → 全局);
  - 冲突更新: 同一 key 的新值替换旧值 ("最喜欢的水果是山竹" → "改喜欢榴莲了");
  - 规则自动捕获 (auto.py) 只把高置信的事实写进全局, 防止闲聊/任务说明污染所有会话。

纯规则, 无 LLM、无外部依赖。抽不出 key 的句子不是错误, 只是不能做同 key 覆盖 ——
模型调用 save_memory 时可显式给 key, 那是主路径 (mem0 也靠模型决定 ADD/UPDATE)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

# 记住类前缀: 不是事实本身
_REMEMBER_PREFIX = re.compile(
    r"^\s*(?:请|帮我|麻烦)?(?:你)?(?:要)?(?:记住|记一下|记下|记好|记得|牢记)(?:了|一下)?\s*[:：,，。]?\s*")
_SUBJ = r"(?:我|用户|主人)"
_CLAUSE_SPLIT = re.compile(r"[，。,；;！!？?\n]")
_VAL_END = r"[^，。,；;！!？?\n]"

# 值 → 所属类别 (推断 "改喜欢榴莲了" 这类不带类别的说法该覆盖哪条)
_CATEGORY_WORDS = {
    "水果": "山竹 榴莲 苹果 香蕉 橙子 橘子 葡萄 西瓜 草莓 芒果 荔枝 龙眼 桃子 梨 菠萝 凤梨 樱桃 车厘子 "
            "猕猴桃 蓝莓 柚子 椰子 火龙果 哈密瓜 柿子 杨梅 石榴 木瓜 李子 杏 枇杷 百香果 牛油果",
    "颜色": "红色 蓝色 绿色 黄色 紫色 黑色 白色 粉色 橙色 灰色 棕色 青色 金色 银色",
    "饮料": "咖啡 奶茶 可乐 雪碧 绿茶 红茶 乌龙茶 果汁 啤酒 牛奶 豆浆 美式 拿铁 茶",
    "编程语言": "python golang go rust java javascript typescript c++ c# kotlin swift ruby php lua scala",
    "编辑器": "vim neovim emacs vscode cursor sublime pycharm idea",
    "运动": "跑步 游泳 篮球 足球 羽毛球 乒乓球 网球 骑行 健身 瑜伽 爬山 滑雪",
}
_WORD2CAT = {w: c for c, ws in _CATEGORY_WORDS.items() for w in ws.split()}

# 身份类 key 的同义归一
_KEY_ALIAS = {
    "姓名": "名字", "名称": "名字", "称呼": "名字", "昵称": "名字", "name": "名字",
    "工作": "职业", "岗位": "职业", "职位": "职业",
    "所在城市": "城市", "所在地": "城市", "常住地": "城市",
    "手机": "手机号", "电话": "手机号", "邮件": "邮箱", "email": "邮箱",
}
_PROFILE_KEYS = (r"名字|姓名|昵称|称呼|生日|时区|城市|所在城市|所在地|家乡|职业|工作|岗位|职位|公司|"
                 r"邮箱|母语|手机号?|电话|宠物|血型|星座|专业|学校|身高|体重|年龄|孩子|老婆|老公|对象")
_PROFESSIONS = (r"工程师|程序员|开发者|开发|老师|教师|学生|研究生|博士生|医生|护士|设计师|产品经理|项目经理|"
                r"经理|律师|会计|研究员|作家|运维|架构师|分析师|创业者|老板|销售|编辑|记者|翻译|厨师|司机")
_GENERIC_PLACES = {"公司", "家", "家里", "学校", "单位", "这里", "那里", "这边", "那边", "外面", "路上"}

_PATTERNS = [
    # 我最喜欢的水果是山竹 / 用户爱吃的水果: 山竹
    ("like_of", re.compile(
        rf"^{_SUBJ}?(?:现在|目前)?(?:最)?(?:喜欢|爱|偏爱|喜爱)(?P<verb>吃|喝|看|听|玩|用|穿)?的"
        rf"(?P<obj>[一-鿿A-Za-z]{{1,10}}?)(?:是|为|[:：])\s*(?P<val>{_VAL_END}{{1,40}})")),
    # 我(现在)改喜欢榴莲了 / 我最爱喝咖啡
    ("like", re.compile(
        rf"^{_SUBJ}?(?:现在|已经|又|其实|最近|目前)?(?P<chg>改|换|变)?(?:成|为)?(?:了)?(?:最)?"
        rf"(?:喜欢|爱|偏爱|喜爱)(?:上)?(?:了)?(?P<verb>吃|喝|看|听|玩|用|穿)?"
        rf"(?P<val>[^，。,；;！!？?\n的]{{1,20}}?)(?:了)?\s*$")),
    # 回复语言: 以后回答都用英文 / 回复请用中文
    ("lang", re.compile(
        r"(?:回答|回复|说话|交流|答复)(?:都|请|一律|统一|全部)?(?:改)?(?:用|使用|成)"
        r"(?P<val>中文|英文|英语|日语|日文|粤语|简体中文|繁体中文|简体|繁体)")),
    # 我的名字是张三 / 用户的职业: 运维
    ("kv", re.compile(
        rf"^{_SUBJ}的?(?P<k>{_PROFILE_KEYS})(?:是|叫|为|[:：])\s*(?P<val>{_VAL_END}{{1,40}})")),
    # 我叫张三
    ("name", re.compile(rf"^{_SUBJ}(?:叫|名叫|的名字叫)(?P<val>[一-鿿A-Za-z·]{{1,12}})$")),
    # 我是一名后端工程师
    ("job", re.compile(
        rf"^{_SUBJ}(?:现在)?是(?:一名|一个|个|位|一位)?(?P<val>[一-鿿A-Za-z]{{0,10}}(?:{_PROFESSIONS}))$")),
    # 我住在杭州 / 我在深圳工作
    ("city", re.compile(
        rf"^{_SUBJ}(?:现在|目前)?(?:(?:住在|定居在?)(?P<val>[一-鿿]{{2,8}}?)"
        rf"|在(?P<val2>[一-鿿]{{2,8}}?)(?:工作|生活|上班|定居))$")),
]
_PLAIN_KV = re.compile(r"^(?:用户|主人|我)?的?(?P<k>[^:：=，。,\s]{1,15})\s*[:：=]\s*(?P<val>[^\n]{1,60})$")
_CHANGE = re.compile(r"改|换|变|现在|已经|不再|其实|最近")
# 明确的长期偏好标记 (规则自动捕获只认这些; "禁止/必须" 常出现在一次性任务说明里, 不算)
_DURABLE = re.compile(r"^(?:以后|今后|往后|从现在起|每次都?|一律|永远|总是|始终|我习惯|我偏好|我希望你)")


@dataclass
class Fact:
    key: Optional[str]          # 归一后的事实名, 如 "喜欢的水果"; None = 抽不出
    value: str                  # 事实值, 如 "山竹"; 抽不出 key 时为整句
    text: str                   # 展示文本: "喜欢的水果: 山竹" 或清洗后的原句
    kind: str = "text"          # 命中的规则名
    changed: bool = False       # 句子里带 "改/换/现在" 等更新语气
    explicit: bool = False      # 用户说了 "记住"


def strip_remember(text: str) -> tuple[str, bool]:
    t = (text or "").strip()
    m = _REMEMBER_PREFIX.match(t)
    return (t[m.end():].strip(), True) if m and m.end() else (t, False)


def norm_key(key: str) -> str:
    """'最喜欢的 水果' / '爱吃的水果' / 'favorite fruit' 归成同一写法, 用于同 key 判断。"""
    from .recall import normalize
    k = normalize(str(key or "")).strip().strip("：:= ")
    k = re.sub(r"^(?:用户|主人|我)的?", "", k)
    k = re.sub(r"[\s_\-·]+", "", k)
    k = k.replace("最喜欢", "喜欢")
    k = re.sub(r"^喜欢(?:吃|喝|看|听|玩|用|穿)的", "喜欢的", k)
    return _KEY_ALIAS.get(k, k)


def category_of(value: str) -> Optional[str]:
    v = (value or "").strip().lower()
    if v in _WORD2CAT:
        return _WORD2CAT[v]
    for w, c in _WORD2CAT.items():
        if len(w) >= 2 and w in v:
            return c
    return None


def _clean_val(v: str) -> str:
    return re.sub(r"(?:了|啦|呢|哦|吧)+$", "", (v or "").strip()).strip(" ：:")


def _from_clause(clause: str) -> Optional[Fact]:
    c = clause.strip()
    for kind, pat in _PATTERNS:
        m = pat.search(c)
        if not m:
            continue
        val = _clean_val(m.group("val") or (m.groupdict().get("val2") or ""))
        if not val or val.startswith(("你", "他", "她", "它", "这", "那", "什么", "谁")):
            continue
        if kind == "like_of":
            key = norm_key("喜欢的" + m.group("obj"))
        elif kind == "like":
            cat = category_of(val)
            if not cat:
                continue            # "我喜欢这个方案" 不是档案事实
            key = norm_key("喜欢的" + cat)
        elif kind == "lang":
            key, val = "回复语言", {"英语": "英文", "日文": "日语"}.get(val, val)
        elif kind == "kv":
            key = norm_key(m.group("k"))
        elif kind == "name":
            key = "名字"
        elif kind == "job":
            key = "职业"
        elif kind == "city":
            if val in _GENERIC_PLACES:
                continue
            key = "城市"
        else:
            continue
        return Fact(key, val, f"{key}: {val}", kind, bool(_CHANGE.search(c)))
    return None


def extract(text: str) -> Fact:
    """抽不出 key 时返回 key=None、text=清洗后的整句。"""
    body, explicit = strip_remember(text)
    body = re.sub(r"\s+", " ", body).strip()
    m = _PLAIN_KV.match(body)          # "喜欢的水果: 山竹" / "名字=张三" (模型常这样写)
    if m:
        k = norm_key(m.group("k"))
        if k.startswith("喜欢的") or k == "回复语言" or re.fullmatch(_PROFILE_KEYS, k):
            v = _clean_val(m.group("val"))
            return Fact(k, v, f"{k}: {v}", "plain_kv", False, explicit)
    for clause in _CLAUSE_SPLIT.split(body):
        f = _from_clause(clause)
        if f:
            f.explicit = explicit
            return f
    return Fact(None, body, body, "text", bool(_CHANGE.search(body)), explicit)


def is_durable_preference(text: str) -> bool:
    body, explicit = strip_remember(text)
    return explicit or bool(_DURABLE.search(body))
