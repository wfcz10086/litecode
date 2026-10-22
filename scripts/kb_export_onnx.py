"""把 text2vec-base-chinese-paraphrase (sentence-transformers / PyTorch) 导出成 ONNX, 供 plugins/kb/embed.py 用。
在一次性容器里跑 (scripts/kb_export_onnx.sh), 运行时不需要 torch。用法: python kb_export_onnx.py <源目录> <输出目录>

输出: model.onnx (fp32, 输入 input_ids/attention_mask/token_type_ids, 输出 last_hidden_state) + 分词词表
      (tokenizer.json 优先, vocab.txt 兜底) + selftest.json (几句话的参考向量, 测试用来核对分词与输出一致)。
int8 动态量化实测与原模型余弦只有 0.89~0.94 (2026-10-22), 检索质量损失明显, 所以保留 fp32。
"""
import json
import shutil
import sys
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer

src, out = Path(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
model = AutoModel.from_pretrained(src).eval()
tok = AutoTokenizer.from_pretrained(src)
names = ["input_ids", "attention_mask", "token_type_ids"]
enc = tok(["你好世界, 这是测试", "LiteCode 知识库 ABC-123"], padding=True, return_tensors="pt")
torch.onnx.export(model, tuple(enc[k] for k in names), str(out / "model.onnx"), input_names=names,
                  output_names=["last_hidden_state"],
                  dynamic_axes={k: {0: "b", 1: "s"} for k in names + ["last_hidden_state"]},
                  opset_version=17, do_constant_folding=True)
for f in ["vocab.txt", "tokenizer.json", "config.json", "tokenizer_config.json", "special_tokens_map.json",
          "sentence_bert_config.json"]:
    if (src / f).exists():
        shutil.copy(src / f, out / f)
if (src / "1_Pooling").is_dir():
    shutil.copytree(src / "1_Pooling", out / "1_Pooling", dirs_exist_ok=True)

texts = ["如何申请年假？", "员工每年享有15天带薪年假, 需提前一周在OA系统提交申请。", "今天天气不错",
         "The quick brown fox 跳过了 lazy dog 123.45%", "知识库检索要带出处", "ＡＢＣ全角字符和emoji😀测试",
         "Café naïve résumé — “引号” ①②③ \t 换行\n结束"]
enc = tok(texts, padding=True, truncation=True, max_length=256, return_tensors="pt")
with torch.no_grad():
    h = model(**{k: enc[k] for k in names}).last_hidden_state
m = enc["attention_mask"].unsqueeze(-1).float()
ref = ((h * m).sum(1) / m.sum(1)).numpy()
json.dump({"texts": texts, "ids": [tok(t, truncation=True, max_length=256)["input_ids"] for t in texts],
           "ref": np.round(ref, 6).tolist()}, open(out / "selftest.json", "w"), ensure_ascii=False)
(out / "SOURCE.txt").write_text(f"由 {src} 导出 (scripts/kb_export_onnx.sh); 原模型: shibing624/text2vec-base-chinese-"
                                "paraphrase (ERNIE-3.0-base, 768 维, mean pooling, max_seq_length=256)\n",
                                encoding="utf-8")
print("ok", out)
