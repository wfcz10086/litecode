# TOOLS.md — 本 Session 环境备忘

## 工程路径
<!-- 在 session 初始化时由系统自动填充 -->

## 服务地址
<!-- 记录本 session 启动的服务端口和URL, 如 LiteCode Server: http://127.0.0.1:18789 / Web UI: http://127.0.0.1:18790 -->

## 环境变量
<!-- 记录重要的环境配置 -->

## 浏览器自动化

Browser Agent 服务（端口 19000，细节见 `browser-automation` skill）：
- 启动: `nohup python3 /opt/litecode/skills/browser-automation/browser_server.py > /tmp/bas.log 2>&1 &`
- 检查: `curl -s http://127.0.0.1:19000/health`
- VNC 可视: 用户登录 Web UI 后打开同源入口 `/vnc/` (如 `http://<host>:18790/vnc/`); noVNC 本身只监听 127.0.0.1

## 备注
<!-- 任何与本 session 相关的环境信息 -->
