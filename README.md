# Stock Strategy API

面向沪深 300 的规则型股票策略服务。首个策略为“强势向上跳空缺口”，采用 D0 发现、D1 验证承接、D2 最早交易，并在未来 1～5 个交易日的事件回测中严格遵守 A 股 T+1。

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

`scan` 默认按策略配置覆盖候选和入场资格窗口，并按时间顺序补扫 D0、推进历史信号。当前默认配置为 D0 + D1 确认 + D2 入场，共回看 3 个交易日。传入 `--lookback-trading-days 1` 可执行显式单日扫描。

默认推荐只包含 D1 已确认、下一交易日仍可参与且非衰竭风险的 `entry_eligible` 信号。0.5% 缺口和 1.5 倍量比是 SHORT_GAP V0 工程入口；原 1% 缺口、2 倍量比及严格趋势平台条件保留为 STRICT_GAP 高质量标签，不代表已证明的最优阈值。

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
