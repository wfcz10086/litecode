"""运行中插话 (steer): 把用户在回合进行中发来的补充/纠正接进上下文。

队列在 lib/session.py (push_steer / take_steers)。agent 循环在两个时机调用 apply_steers:
- 一批工具执行完、下一次调用模型之前;
- 模型给出收尾正文、本该结束回合时 —— 有插话就不结束, 接着按插话继续。
"""
from lib.session import take_steers

STEER_HEAD = "[用户在任务进行中插话]"
STEER_TAIL = ("（这是对当前任务的补充或纠正：先用一句话回应用户（如果是问题就直接答；如果是要求，说清你会怎么调整；"
              "如果它已经指定了要你回复什么，就直接按它回复，不要再另加一句确认），"
              "再按它调整接下来的做法；原计划中没被它否定的部分照常继续，"
              "不要从头重来，也不要把它当成一个全新的任务。如果是在纠正你的做法，当场照做并 save_memory 记一条。）")


def build_steer_message(texts: list) -> dict:
    body = "\n".join(f"- {t}" for t in texts) if len(texts) > 1 else texts[0]
    return {"role": "user", "content": f"{STEER_HEAD}\n{body}\n\n{STEER_TAIL}"}


def apply_steers(ctx) -> list:
    """取出本会话待处理的插话, 作为一条用户消息追加到上下文 (并入库)。返回插话原文列表。"""
    sid = getattr(ctx, "session_id", None)
    if not sid:
        return []
    texts = [t for t in take_steers(sid) if t and t.strip()]
    if not texts:
        return []
    msg = build_steer_message(texts)
    ctx.messages.append(msg)
    ctx.new_msgs.append(msg)
    return texts
