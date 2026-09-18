#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo "=== smoke: web_assets2 静态检查 ==="

# 1. 无 prompt/confirm/alert
if grep -rE 'prompt\(|confirm\(|alert\(' litecodeext/web_assets2/ 2>/dev/null; then
  echo "FAIL: 发现 prompt/confirm/alert 调用"; exit 1
fi

# 2. 无 inline 事件
if grep -rE 'on(click|change|input|submit)=' litecodeext/web_assets2/ 2>/dev/null; then
  echo "FAIL: 发现 inline 事件属性"; exit 1
fi

# 3. 无 .jsx 引用
if grep -rn '\.jsx' litecodeext/web_assets2/ 2>/dev/null; then
  echo "FAIL: 发现 .jsx 引用"; exit 1
fi

# 4. 无 JS 文件超 300 行
overflow=$(find litecodeext/web_assets2 -name '*.js' | xargs wc -l | awk '$1>300 && $2!="total" {print $2":"$1}')
if [ -n "$overflow" ]; then
  echo "FAIL: 单文件超 300 行:"; echo "$overflow"; exit 1
fi

# 5. 模块入口存在
for f in litecodeext/web_assets2/main.js litecodeext/web_assets2/index.html litecodeext/web_assets2/store/signal.js; do
  [ -f "$f" ] || { echo "FAIL: 缺 $f"; exit 1; }
done

echo "smoke PASS ✓"
