#!/bin/bash
# IndexTTS 实测: 普通朗读 / 指定情绪 / 参考音频克隆, 每段用 SenseVoice 反向识别
cd "$(dirname "$0")"
H=http://127.0.0.1:18013; A=http://127.0.0.1:18011
asr() { curl -s $A/v1/audio/transcriptions -F file=@$1 -F language=zh | python3 -c 'import json,sys;print(json.load(sys.stdin)["text"])'; }
dur() { ffprobe -v error -show_entries format=duration -of csv=p=0 $1; }
curl -s $H/v1/audio/voices; echo
T1="欢迎使用语音合成服务，这是一段普通朗读测试，今天是十月二十三日。"
curl -s -o tts_plain.wav -w "plain: HTTP %{http_code} %{time_total}s\n" $H/v1/audio/speech -H 'Content-Type: application/json' \
  -d "{\"input\":\"$T1\",\"voice\":\"voice_01\"}"; echo "  时长 $(dur tts_plain.wav)s  识别: $(asr tts_plain.wav)"
T2="太好了！我们终于赢得了比赛，大家辛苦了！"
curl -s -o tts_emo.wav -w "emotion: HTTP %{http_code} %{time_total}s\n" $H/v1/audio/speech -H 'Content-Type: application/json' \
  -d "{\"input\":\"$T2\",\"voice\":\"voice_01\",\"instructions\":\"非常兴奋激动，开心地大喊\",\"emo_alpha\":0.8}"; echo "  时长 $(dur tts_emo.wav)s  识别: $(asr tts_emo.wav)"
T3="这是一段声音克隆测试，用参考音频的音色来朗读新的句子。"
curl -s -o tts_clone.wav -w "clone: HTTP %{http_code} %{time_total}s\n" $H/v1/audio/clone \
  -F "input=$T3" -F prompt_wav=@zh_test.wav; echo "  时长 $(dur tts_clone.wav)s  识别: $(asr tts_clone.wav)"
curl -s -o tts_slow.wav -w "slow(speed=0.8): HTTP %{http_code} %{time_total}s\n" $H/v1/audio/speech -H 'Content-Type: application/json' \
  -d "{\"input\":\"$T1\",\"voice\":\"voice_01\",\"speed\":0.8}"; echo "  时长 $(dur tts_slow.wav)s"
