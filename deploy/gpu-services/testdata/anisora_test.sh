#!/bin/bash
# AniSora 图生视频实测: 提交 → 轮询 → 下载. 用法: anisora_test.sh <图片> "<提示词>" [size] [输出名]
H=http://127.0.0.1:18014
IMG=$1; P=$2; SIZE=${3:-832*480}; OUT=${4:-anisora_out.mp4}
T0=$(date +%s)
ID=$(curl -s $H/v1/videos -F "prompt=$P" -F image=@$IMG -F size=$SIZE -F frame_num=81 | python3 -c 'import json,sys;print(json.load(sys.stdin)["id"])')
echo "job $ID"
while :; do R=$(curl -s $H/v1/videos/$ID); S=$(echo "$R" | python3 -c 'import json,sys;print(json.load(sys.stdin)["status"])')
  case $S in succeeded|failed) break;; esac; sleep 5; done
echo "$R"; echo "端到端 $(( $(date +%s) - T0 ))s"
[ "$S" = succeeded ] && curl -s -o $OUT $H/v1/videos/$ID/content && ffprobe -v error -show_entries stream=width,height,nb_frames,r_frame_rate -show_entries format=duration -of compact $OUT
