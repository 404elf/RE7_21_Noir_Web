# RE7 / 21 — NOIR Web

独立的浏览器双人 21 点游戏。复用 [RE7_21_Noir 桌面版](https://github.com/404elf/RE7_21_Noir) 的 Python 规则、全部 48 张王牌与 JSON 配置，以 FastAPI / WebSocket 提供联机；前端使用 HTML、CSS 和 JavaScript，无需安装 Pygame、Node.js 或数据库。

![Noir Web 首页](docs/web-noir-home.png)

两个浏览器通过房间码或邀请链接入座，完整打到生命耗尽，可刷新重连、求和、投降与再战。支持独立多房间、私有状态补丁和本地倒计时；界面包含纸牌翻转、拖牌、粒子、可选音效、手机布局与减少动态效果支持。

## 本地运行

需要 Python 3.12+，在虚拟环境中执行：

```sh
python -m pip install -r requirements-web.txt
python -m uvicorn web.app:app --host 127.0.0.1 --port 8000 --workers 1 --ws-max-size 2048 --ws-max-queue 8
```

打开 <http://127.0.0.1:8000>。Docker 运行：

```sh
docker compose -p noir-web up --build -d
```

## 游戏路径与域名

部署到 `https://game.404elf.dev/re7` 时配置：

```env
NOIR_BASE_PATH=/re7
NOIR_ORIGIN=https://game.404elf.dev
```

反向代理须保留 `/re7` 前缀并转发 WebSocket。以后更换 `404elf.dev`，只需更新 Origin、DNS、证书与代理配置，无需改源码或重建镜像。默认服务在根路径，本地 HTTP 测试时 Origin 可留空。[完整运行、配置、部署与换域名说明](docs/WEB.md) · [部署示例](deploy/noir.env.example)

## 验证

```sh
python -m pip install -r requirements-web-dev.txt
python -m playwright install chromium
python -m pytest tests -q
python tests/web_e2e.py
python tests/web_e2e.py --base-path /re7
```

CI 在不安装 Pygame 的环境中验证共享规则、计时、全部王牌、消息隐私、重连、双浏览器完整对局、子路径及标准 Docker 构建。截图和消息字节报告作为 CI 附件保存。

## 当前边界

房间保存在单进程内存中，重启会丢失；只运行一个 worker / 一个副本。当前上限为 64 个双人房间，该上限没有经过容量压测。没有账号、排位、持久回放、旁观或单机 AI。Safari、Firefox、实际移动设备与公网弱网尚未完整验收。默认关闭行动计时；可通过 `timer.json` 启用已有准备/行动超时规则。

本仓库独立发布和部署，不改变原桌面版的 main 或发布流程。复用来源、同步步骤与许可证见 [UPSTREAM.md](UPSTREAM.md) 和 [LICENSE](LICENSE)。
