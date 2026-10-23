"""SenseVoiceSmall HTTP 服务 (FunASR). 接口:
  POST /v1/audio/transcriptions   OpenAI 兼容 (multipart: file, model, language, response_format=json|text|verbose_json)
  POST /api/v1/asr                 与官方 SenseVoice api.py 兼容 (files[], keys, lang, use_itn)
  GET  /health
"""
import io, os, re, time, tempfile, threading
import numpy as np, soundfile as sf, torch, torchaudio
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import PlainTextResponse
from typing import List, Optional
from funasr import AutoModel
from funasr.utils.postprocess_utils import rich_transcription_postprocess

DEVICE = os.environ.get("SENSEVOICE_DEVICE", "cuda:0")
MODEL = os.environ.get("SENSEVOICE_MODEL", "/models/iic/SenseVoiceSmall")
VAD = os.environ.get("SENSEVOICE_VAD", "/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch")
model = AutoModel(model=MODEL, vad_model=VAD,
                  vad_kwargs={"max_single_segment_time": 30000}, device=DEVICE, disable_update=True)
lock = threading.Lock()
app = FastAPI(title="SenseVoice ASR")
LANGS = {"auto", "zh", "en", "yue", "ja", "ko", "nospeech"}
TAG = re.compile(r"<\|([^|]*)\|>")


def load_audio(raw: bytes) -> np.ndarray:
    """soundfile 直读 wav/flac/ogg; 其他格式 (mp3/m4a/webm...) 交给 ffmpeg 转 16k 单声道。"""
    try:
        data, sr = sf.read(io.BytesIO(raw), dtype="float32", always_2d=True)
    except Exception:
        with tempfile.TemporaryDirectory() as d:
            src, out = os.path.join(d, "in.bin"), os.path.join(d, "out.wav")
            open(src, "wb").write(raw)
            if os.system(f"ffmpeg -loglevel error -y -i {src} -ac 1 -ar 16000 {out}") != 0:
                raise HTTPException(400, "无法解码音频")
            data, sr = sf.read(out, dtype="float32", always_2d=True)
    wav = torch.from_numpy(data.mean(1))
    if sr != 16000:
        wav = torchaudio.functional.resample(wav, sr, 16000)
    return wav.numpy()


def run(audio: np.ndarray, lang: str, itn: bool):
    with lock:
        res = model.generate(input=audio, cache={}, language=lang, use_itn=itn, batch_size_s=60, merge_vad=True)
    raw = res[0]["text"] if res else ""
    tags = TAG.findall(raw)
    return raw, rich_transcription_postprocess(raw), tags


@app.get("/health")
def health():
    return {"status": "ok", "model": "iic/SenseVoiceSmall", "device": DEVICE}


@app.post("/v1/audio/transcriptions")
async def transcriptions(file: UploadFile = File(...), model_name: Optional[str] = Form(None, alias="model"),
                         language: Optional[str] = Form(None), response_format: str = Form("json"),
                         prompt: Optional[str] = Form(None), temperature: Optional[float] = Form(None)):
    lang = (language or "auto").lower()
    lang = {"zh-cn": "zh", "cn": "zh", "chinese": "zh", "english": "en", "cantonese": "yue", "japanese": "ja", "korean": "ko"}.get(lang, lang)
    if lang not in LANGS:
        lang = "auto"
    t0 = time.time()
    audio = load_audio(await file.read())
    raw, rich, tags = run(audio, lang, True)
    text = TAG.sub("", raw).strip()  # OpenAI 兼容: 纯文本, 不带情绪 emoji
    if response_format == "text":
        return PlainTextResponse(text)
    if response_format == "verbose_json":
        return {"task": "transcribe", "language": (tags[0] if tags else lang), "duration": len(audio) / 16000,
                "text": text, "rich_text": rich, "raw_text": raw, "tags": tags, "elapsed": round(time.time() - t0, 3)}
    return {"text": text}


@app.post("/api/v1/asr")
async def asr(files: List[UploadFile] = File(...), keys: Optional[str] = Form(None),
              lang: str = Form("auto"), use_itn: bool = Form(False)):
    names = keys.split(",") if keys else [f.filename for f in files]
    out = []
    for name, f in zip(names, files):
        raw, text, _ = run(load_audio(await f.read()), lang if lang in LANGS else "auto", use_itn)
        out.append({"key": name, "raw_text": raw, "clean_text": TAG.sub("", raw), "text": text})
    return {"result": out}
