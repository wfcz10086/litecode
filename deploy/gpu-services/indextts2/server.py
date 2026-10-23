"""IndexTTS-2.5 HTTP 服务. 接口:
  POST /v1/audio/speech        OpenAI 兼容 JSON: {input, voice, response_format=wav|mp3|flac, speed, instructions(情绪描述), lang, emo_alpha}
  POST /v1/audio/clone         multipart: input, prompt_wav(参考音色), [emo_wav], [emo_text], [emo_vector JSON 8 维], [duration_factor], [lang], [response_format]
  GET  /v1/audio/voices        列出内置音色 (examples/*.wav 以及 /app/voices/*.wav)
  GET  /health
emo_vector 8 维顺序: [高兴, 愤怒, 悲伤, 害怕, 厌恶, 忧郁, 惊讶, 平静]
"""
import glob, io, json, os, subprocess, tempfile, threading, time
from typing import Optional
from fastapi import FastAPI, File, Form, UploadFile, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel
from indextts.infer_v2_5 import IndexTTS2

os.chdir("/app")
DEVICE = os.environ.get("INDEXTTS_DEVICE", "cuda:0")
tts = IndexTTS2(cfg_path="checkpoints/config.yaml", model_dir="checkpoints", use_bf16=True, device=DEVICE, use_qwen_emo=True)
lock = threading.Lock()
app = FastAPI(title="IndexTTS-2.5")
MIME = {"wav": "audio/wav", "mp3": "audio/mpeg", "flac": "audio/flac", "opus": "audio/ogg", "aac": "audio/aac"}


def voices():
    v = {}
    for d in ("examples", "voices"):
        for p in sorted(glob.glob(f"/app/{d}/*.wav")):
            v[os.path.splitext(os.path.basename(p))[0]] = p
    return v


def synth(text, spk, lang="ZH", fmt="wav", emo_wav=None, emo_text=None, emo_vector=None, emo_alpha=0.6, duration_factor=1.0):
    if not text or not text.strip():
        raise HTTPException(400, "input 为空")
    fmt = fmt if fmt in MIME else "wav"
    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "out.wav")
        kw = dict(spk_audio_prompt=spk, text=text, output_path=out, lang=lang, duration_factor=duration_factor, verbose=False)
        if emo_wav:
            kw.update(emo_audio_prompt=emo_wav, emo_alpha=emo_alpha)
        elif emo_vector:
            kw.update(emo_vector=emo_vector)
        elif emo_text:
            kw.update(use_emo_text=True, emo_text=emo_text, emo_alpha=emo_alpha)
        t0 = time.time()
        with lock:
            tts.infer(**kw)
        el = time.time() - t0
        if fmt != "wav":
            conv = os.path.join(d, "out." + ("ogg" if fmt == "opus" else fmt))
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", out, conv], check=True)
            out = conv
        data = open(out, "rb").read()
    return Response(data, media_type=MIME[fmt], headers={"X-Elapsed": f"{el:.2f}"})


class SpeechReq(BaseModel):
    input: str
    model: Optional[str] = "indextts-2.5"
    voice: Optional[str] = None
    response_format: Optional[str] = "wav"
    speed: Optional[float] = 1.0
    instructions: Optional[str] = None
    lang: Optional[str] = "ZH"
    emo_alpha: Optional[float] = 0.6
    emo_vector: Optional[list] = None


@app.get("/health")
def health():
    return {"status": "ok", "model": "IndexTTS-2.5", "device": DEVICE, "voices": len(voices())}


@app.get("/v1/audio/voices")
def list_voices():
    return {"voices": list(voices().keys())}


@app.post("/v1/audio/speech")
def speech(req: SpeechReq):
    vs = voices()
    name = req.voice if req.voice in vs else ("voice_01" if "voice_01" in vs else next(iter(vs)))
    speed = req.speed or 1.0
    return synth(req.input, vs[name], (req.lang or "ZH").upper(), req.response_format or "wav",
                 emo_text=req.instructions, emo_vector=req.emo_vector, emo_alpha=req.emo_alpha or 0.6,
                 duration_factor=max(0.5, min(2.0, 1.0 / speed)))


@app.post("/v1/audio/clone")
async def clone(input: str = Form(...), prompt_wav: UploadFile = File(...), emo_wav: Optional[UploadFile] = File(None),
                emo_text: Optional[str] = Form(None), emo_vector: Optional[str] = Form(None), emo_alpha: float = Form(0.6),
                duration_factor: float = Form(1.0), lang: str = Form("ZH"), response_format: str = Form("wav")):
    with tempfile.TemporaryDirectory() as d:
        def save(f, name):
            p = os.path.join(d, name + ".in")
            open(p, "wb").write(f.file.read() if hasattr(f, "file") else f)
            wav = os.path.join(d, name + ".wav")
            subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", p, "-ac", "1", wav], check=True)
            return wav
        spk = save(prompt_wav, "spk")
        emo = save(emo_wav, "emo") if emo_wav is not None else None
        vec = json.loads(emo_vector) if emo_vector else None
        return synth(input, spk, lang.upper(), response_format, emo_wav=emo, emo_text=emo_text, emo_vector=vec,
                     emo_alpha=emo_alpha, duration_factor=duration_factor)
