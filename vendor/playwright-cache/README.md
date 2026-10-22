# Playwright browsers cache (optional)
此目录可用于预先下载 playwright chromium，加速 docker build。

## 使用

如要使用本地 cache 加速：

```bash
# 国内开发者（淘宝镜像）
export PLAYWRIGHT_DOWNLOAD_HOST=https://npmmirror.com/mirrors/playwright
export PLAYWRIGHT_BROWSERS_PATH=$(pwd)/vendor/playwright-cache
python3 -m playwright install chromium
```

之后 `docker build` 会自动 COPY 这里的 chromium 进容器，跳过远程下载。

## 清理

```bash
rm -rf vendor/playwright-cache/*
```

## 注意

- 此目录默认 .gitignore，二进制文件不入库
- 仅 .gitkeep 入库以保证目录存在

