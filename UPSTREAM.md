# 复用来源与规则同步

- 桌面项目：[404elf/RE7_21_Noir](https://github.com/404elf/RE7_21_Noir)
- 游戏机制基线：v1.5.3，提交 `7b921cb`。
- 本次 Web 导出来源：`codex/browser-multiplayer` 分支，提交 [`4687f96571718c6ffa10e6b4da95b9bbe49d8741`](https://github.com/404elf/RE7_21_Noir/commit/4687f96571718c6ffa10e6b4da95b9bbe49d8741)。
- 授权文件：[MIT LICENSE](LICENSE)，保留原有 copyright notice。

初始导出保持 `re7_21.py`、`match.py`、`cards.py`、`presentation_rules.py`、`rules.py`、`app_paths.py`、`config.json` 和 `timer.json` 与上述 Web 开发提交逐字节一致。`re7_21.py` 保留上游结构，包含未用于 Web 运行入口的历史桌面/TCP 定义；Web 只调用其中的 GameState 规则。Pygame 导入为可选，不属于本仓库的运行或测试依赖。

Web 服务、前端、Docker、部署示例和 Web 测试由原开发分支迁入；README、CI 和说明已调整为独立 Web 项目。原桌面打包、自动更新、历史文件和 AI 不在本仓库运行范围内。

玩家自定义功能复用同一来源提交下的 `presets/` 全部 8 份 JSON，原样保留预设内容。房间校验与网页编辑入口位于 `web/`，共享规则引擎与根目录默认配置保持不变。

`tests/test_match_core.py` 从原 `tests/test_match.py` 保留 8 项无桌面依赖的 MatchTests：行动/整场/每局计时、加秒、结算、再战、日志隐私、效果一致性及配置校验。只移除了依赖桌面历史与 TCP 的两项测试和对应导入；保留测试的方法体与断言不变。原完整桌面测试仍在上游仓库。

## 后续同步机制

两个仓库分别维护，不自动追踪上游 main。桌面版新增机制时：

1. 记录要同步的桌面提交，比较共享规则与配置的实际差异。
2. 只移植规则、牌池或计时的必要变更，保留 Web 适配与私有投影；不整体覆盖 Web 仓库。
3. 补充对应规则回归，运行核心/Web 测试和双浏览器完整对局，再记录同步版本。

部署始终从本仓库构建；不需要将 Web 实现合并到桌面仓库。域名、路径和可信代理属于部署配置，独立于上游版本。
