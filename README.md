# Stock Strategy API

面向沪深 300 的规则型股票策略服务。首个策略为“强势向上跳空缺口”，采用 D0 发现、D1 验证承接、D2 正常入场，并独立评价 D3 延续入场；未来 1～5 个交易日的事件回测严格遵守 A 股 T+1。

本项目独立运行，不依赖任何外部本地代码仓库，也不包含机器学习训练框架。

## 安装

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
```

## 启动 API

```bash
uvicorn stock_strategy_api.main:app --reload
```

接口文档：`http://127.0.0.1:8000/docs`。

## 企业微信智能机器人推送

每日策略脚本使用企业微信智能机器人的 WebSocket 长连接主动推送，不再使用群机器人 webhook。运行环境需要提供：

- `WECOM_BOT_ID`：智能机器人的 Bot ID；
- `WECOM_BOT_SECRET`：智能机器人的 Secret；
- `WECOM_BOT_CHAT_ID`：接收会话，单聊填写成员 userid，群聊填写 chatid；
- `WECOM_BOT_WS_URL`：可选，默认 `wss://openws.work.weixin.qq.com`。

凭证只应通过受限环境文件或密钥管理服务注入，不应写入仓库。脚本先完成 `aibot_subscribe` 鉴权，再通过 `aibot_send_msg` 发送 Markdown；只有企业微信回执 `errcode=0` 后才会把策略动作标记为成功。

生产或长期运行前复制 `.env.example` 为 `.env`，至少确认数据目录、SQLite 路径、数据新鲜度和回测费用。运行数据全部写入本项目自己的 `data/`，不会读取其他本地仓库。

## CLI

```bash
stock-strategy sync-data --as-of 2026-08-10
stock-strategy scan --strategy strong_gap_up_v1 --as-of 2026-08-10
stock-strategy advance-signals --as-of 2026-08-10
stock-strategy backtest --strategy strong_gap_up_v1 --start 2025-01-01 --end 2025-12-31
stock-strategy show-run --run-id RUN_ID
```

数据同步和回测是长任务，只由 CLI 触发；HTTP API 只读取已物化结果。

`scan` 默认按策略配置覆盖候选、D1确认、D2重新评价和D3延续入场窗口，并按时间顺序推进历史信号，当前回看4个交易日。传入 `--lookback-trading-days 1` 可执行显式单日扫描。

默认 `actionable` 推荐包含 D2 正常入场 `entry_eligible` 和经D2新增行情独立裁决的D3 `continuation_entry`，并排除衰竭风险。D3延续入场要求原始缺口仍有效、D2收盘位置不低于60%、收盘不低于 `gap_top`，且 `D2.close / D0.close - 1 < 10%`。10%只影响当前入场有效性，不改变缺口结构有效性。

0.5% 缺口和 1.5 倍量比是 SHORT_GAP V0 工程入口；原 1% 缺口、2 倍量比及严格趋势平台条件保留为 STRICT_GAP 高质量标签，不代表已证明的最优阈值。

## 数据与验证边界

- 缺口几何使用未复权日线，前置趋势使用前复权日线。
- 当前沪深 300 成分快照用于历史回放时会明确标记幸存者偏差。
- 离线 fixture、真实网络采集和 PIT 历史股票池验证分别报告，不能互相替代。
- 服务输出属于规则型观察信息，不是自动交易指令。

当前短线策略口径见 [docs/short_gap_adjustment_plan.md](docs/short_gap_adjustment_plan.md)，任务与验收见 [docs/short_gap_adjustment_task.md](docs/short_gap_adjustment_task.md)。原始服务基线仍保留在 [docs/plan.md](docs/plan.md) 与 [docs/task.md](docs/task.md)。

## 新增策略

1. 在 `src/stock_strategy_api/strategies/<strategy_id>/` 内实现配置、检测、生命周期、解释和测试。
2. 实现 `strategies/base.py` 的统一 `Strategy` 协议，输出 DTO，不向 API 泄露 DataFrame。
3. 只在 `strategies/registry.py` 注册一次；CLI 与 API 会从同一注册表发现策略。
4. 为新策略补齐离线扫描、生命周期、API 与无前视回测验收后再启用。
