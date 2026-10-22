"""plugins/kb/embed.py — 本地向量模型 (text2vec-base-chinese-paraphrase, ERNIE-3.0-base, 768 维)。

运行时只依赖 onnxruntime + numpy (生产镜像已带), 不装 torch / transformers:
  - 模型: 由 pytorch_model.bin 一次性导出的 ONNX (见 scripts/kb_export_onnx.sh), 输出 last_hidden_state;
  - 分词: 下面的纯 Python BERT WordPiece (与 transformers BertTokenizer 逐 id 对齐, 测试见 test_kb.py);
  - 池化: mean pooling + L2 归一化, 余弦 = 点积。
模型训练长度 256 token: 超长文本按 254 token 分段分别编码, 再按长度加权平均 (块尾内容也进向量,
避免原项目 4000 字块只有开头被编码的问题)。进程内单例懒加载, 首次调用才读模型。
"""
from __future__ import annotations

import os
import threading
import unicodedata
from pathlib import Path

import numpy as np

MAX_LEN = 256
DIM = 768

_lock = threading.Lock()
_inst: dict = {}


# ── 分词 (BERT BasicTokenizer + WordPiece, do_lower_case=True) ───────────

def _is_cjk(cp: int) -> bool:
    return (0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or 0x20000 <= cp <= 0x2A6DF
            or 0x2A700 <= cp <= 0x2B73F or 0x2B740 <= cp <= 0x2B81F or 0x2B820 <= cp <= 0x2CEAF
            or 0xF900 <= cp <= 0xFAFF or 0x2F800 <= cp <= 0x2FA1F)


def _is_punct(ch: str) -> bool:
    cp = ord(ch)
    if 33 <= cp <= 47 or 58 <= cp <= 64 or 91 <= cp <= 96 or 123 <= cp <= 126:
        return True
    return unicodedata.category(ch).startswith("P")


def _is_ws(ch: str) -> bool:
    return ch in " \t\n\r" or unicodedata.category(ch) == "Zs"


def _is_ctrl(ch: str) -> bool:
    if ch in "\t\n\r":
        return False
    return unicodedata.category(ch) in ("Cc", "Cf")


def basic_tokens(text: str) -> list[str]:
    out = []
    for ch in text:
        cp = ord(ch)
        if cp == 0 or cp == 0xFFFD or _is_ctrl(ch):
            continue
        if _is_ws(ch):
            out.append(" ")
        elif _is_cjk(cp):
            out.append(f" {ch} ")
        else:
            out.append(ch)
    toks = []
    for w in "".join(out).split():
        w = "".join(c for c in unicodedata.normalize("NFD", w.lower()) if unicodedata.category(c) != "Mn")
        cur = ""
        for ch in w:
            if _is_punct(ch):
                if cur:
                    toks.append(cur)
                    cur = ""
                toks.append(ch)
            else:
                cur += ch
        if cur:
            toks.append(cur)
    return toks


class WordPiece:
    def __init__(self, vocab_file: Path):
        """优先读同目录 tokenizer.json 的词表 (模型训练用的 fast tokenizer):
        这个模型的 vocab.txt 比 tokenizer.json 少一行, 从第 12084 个词起 id 整体错一位。"""
        tj = Path(vocab_file).with_name("tokenizer.json")
        if tj.is_file():
            import json
            self.vocab = dict(json.loads(tj.read_text(encoding="utf-8"))["model"]["vocab"])
        else:
            self.vocab = {}
            for i, line in enumerate(Path(vocab_file).read_text(encoding="utf-8").split("\n")):
                if line:
                    self.vocab[line] = i
        self.unk = self.vocab["[UNK]"]
        self.cls = self.vocab["[CLS]"]
        self.sep = self.vocab["[SEP]"]

    def ids(self, text: str) -> list[int]:
        """不含 [CLS]/[SEP] 的 id 序列 (不截断)。"""
        out = []
        for tok in basic_tokens(text):
            if len(tok) > 100:
                out.append(self.unk)
                continue
            start, pieces, bad = 0, [], False
            while start < len(tok):
                end, cur = len(tok), None
                while start < end:
                    sub = tok[start:end] if start == 0 else "##" + tok[start:end]
                    if sub in self.vocab:
                        cur = self.vocab[sub]
                        break
                    end -= 1
                if cur is None:
                    bad = True
                    break
                pieces.append(cur)
                start = end
            out.extend([self.unk] if bad else pieces)
        return out

    def encode(self, text: str, max_len: int = MAX_LEN) -> list[int]:
        return [self.cls] + self.ids(text)[: max_len - 2] + [self.sep]


# ── 模型 ──────────────────────────────────────────────────────────

class Embedder:
    def __init__(self, model_dir: Path):
        import onnxruntime as ort
        self.dir = Path(model_dir)
        self.tok = WordPiece(self.dir / "vocab.txt")
        so = ort.SessionOptions()
        so.intra_op_num_threads = int(os.environ.get("LITECODE_KB_THREADS", min(4, os.cpu_count() or 2)))
        so.inter_op_num_threads = 1
        so.enable_cpu_mem_arena = False      # 不留内存池: 批次推理完把激活内存还给系统 (实测常驻少 ~400MB)
        self.sess = ort.InferenceSession(str(self.dir / "model.onnx"), so, providers=["CPUExecutionProvider"])

    def _run(self, seqs: list[list[int]]) -> np.ndarray:
        n, L = len(seqs), max(len(s) for s in seqs)
        ids = np.zeros((n, L), dtype=np.int64)
        mask = np.zeros((n, L), dtype=np.int64)
        for i, s in enumerate(seqs):
            ids[i, :len(s)] = s
            mask[i, :len(s)] = 1
        h = self.sess.run(None, {"input_ids": ids, "attention_mask": mask,
                                 "token_type_ids": np.zeros_like(ids)})[0]
        m = mask[..., None].astype(np.float32)
        return (h * m).sum(1) / np.maximum(m.sum(1), 1e-9)

    def encode(self, texts: list[str], batch: int = 16) -> np.ndarray:
        """→ (n, 768) float32, 已 L2 归一化。超过 254 token 的文本分段编码后加权平均。"""
        body = MAX_LEN - 2
        segs, owner, weight = [], [], []
        for i, t in enumerate(texts):
            ids = self.tok.ids(t or "") or [self.tok.unk]
            for k in range(0, len(ids), body):
                part = ids[k:k + body]
                segs.append([self.tok.cls] + part + [self.tok.sep])
                owner.append(i)
                weight.append(len(part))
        order = sorted(range(len(segs)), key=lambda j: len(segs[j]))   # 长度相近的放一批, 少补零
        vecs = np.zeros((len(segs), DIM), dtype=np.float32)
        for k in range(0, len(order), batch):
            idx = order[k:k + batch]
            vecs[idx] = self._run([segs[j] for j in idx])
        out = np.zeros((len(texts), DIM), dtype=np.float32)
        for j, i in enumerate(owner):
            out[i] += vecs[j] * weight[j]
        out /= np.maximum(np.linalg.norm(out, axis=1, keepdims=True), 1e-9)
        return out


def _reaper() -> None:
    """闲置超过 kb.model_idle_minutes (默认 10, 0=不释放) 就卸载模型, 把内存还给系统; 下次用时再加载 (~2 秒)。"""
    import gc
    import time
    from . import cfg
    while True:
        time.sleep(60)
        idle = float(cfg().get("model_idle_minutes") or 0) * 60
        with _lock:
            if idle and _inst.get("emb") is not None and time.time() - _inst["used"] > idle:
                _inst.update(emb=None, dir=None)
                gc.collect()
                try:                         # glibc 不主动把释放的堆还给系统, 不 trim 的话 RSS 只降 ~200MB
                    import ctypes
                    ctypes.CDLL("libc.so.6").malloc_trim(0)
                except Exception:  # noqa: BLE001  非 glibc 平台
                    pass


def get() -> Embedder:
    """进程内单例 (闲置自动卸载); 模型缺失抛 RuntimeError (调用方转成用户可读的错误)。"""
    import time
    from . import model_dir
    d = model_dir()
    if d is None:
        raise RuntimeError("未找到本地向量模型: 桌面版首次联网后会在后台自动下载模型包 (看托盘图标的提示, 下载完即可用); "
                           "服务器版部署见 docs/KNOWLEDGE_BASE.md")
    with _lock:
        if _inst.get("emb") is None or _inst.get("dir") != str(d):
            _inst.update(emb=None)
            _inst.update(emb=Embedder(d), dir=str(d))
            if not _inst.get("reaper"):
                _inst["reaper"] = True
                threading.Thread(target=_reaper, name="kb-embed-reaper", daemon=True).start()
        _inst["used"] = time.time()
        return _inst["emb"]


def encode(texts: list[str]) -> np.ndarray:
    return get().encode(texts)


def to_pg(vec) -> str:
    """numpy 向量 → pgvector 文本字面量。"""
    return "[" + ",".join(f"{x:.6f}" for x in vec) + "]"
