# 股票策略集 FastAPI 服务开发计划

> 文档状态：实现与验收基线
> 首个策略：`strong_gap_up_v1`（强势向上跳空缺口）

## 1. 目标

建设一个独立的 Python/FastAPI 股票策略服务。每个策略在自己的目录内维护规则、配置、结果解释和测试，通过统一策略注册表和统一 API 向前端提供：

- 策略目录及策略说明；
- 指定交易日的观察信号、确认推荐和失效信号；
- 股票基础信息及触发该推荐的可解释指标；
- 规则型历史回测结果；
- 数据时间、交易日历来源和结果质量提示。

首期只实现沪深 300 范围内的“强势向上跳空缺口”策略。它是确定性规则筛选，不进入机器学习或通用量化研究框架。

## 2. 已确认的产品约束

1. 仅扫描沪深 300 成分股。
2. 排除 ST、`*ST`、退市整理股票、上市不足 60 个自然日的股票、北交所股票和当日停牌股票。
3. 缺口形成日记为 D0；完整回补观察窗口是其后的 D1、D2、D3 三个沪深市场交易日。
4. 完整回补定义为观察日最低价 `low <= D-1.high`。等于缺口下沿也视为回补。
5. 推荐和回测必须遵守 A 股 T+1，并由真实交易日历推进，不能用自然日或仅跳过周末的办法替代。
6. `/Users/fuziqiang/Desktop/stock-analysis` 只能作为代码来源。所需代码必须裁剪、复制到本项目并改为项目内模块；运行期不得依赖其绝对路径、`sys.path` 注入、软链接或本地路径包依赖。
7. 不复制或接入 `stock-analysis` 的模型训练、标签、特征工程、SHAP、MLflow、模型文件及组合优化能力。
8. FastAPI 面向前端提供读接口；数据同步、策略扫描和回测默认由 CLI/批处理触发，避免公开接口直接启动长任务。

## 3. 非目标

- 不实现自动下单、券商账户接入、持仓管理或实盘交易。
- 不宣称策略收益或提供确定性投资建议。
- 不做机器学习预测、因子挖掘、行业中性化或模型排名。
- 首期不扫描全 A 股、中证 500、中证 1000、港股、美股或北交所。
- 不开发前端页面。
- 不在 API 请求中同步抓取 300 只股票并现场计算；API 只读取已经物化的扫描结果。

## 4. 截图规则还原

截图按 `IMG_2380` 至 `IMG_2395`、最后 `IMG_2399` 的叙事顺序还原。`IMG_2399` 是策略总结，不是新的规则分支。

| 截图内容 | 可计算规则或产品表达 |
|---|---|
| 次日最低价高于前日最高价 | D0 的 `low > D-1.high`，形成向上实体缺口 |
| 一轮上涨后横盘平台 | D0 前存在上涨窗口和收敛的平台窗口 |
| 跳空高开、爆量 | 缺口宽度达到阈值，D0 成交量显著高于过去 20 个交易日中位数 |
| 第一天观察、第二天验证、第三天确认 | D1/D2/D3 生命周期，不允许在 D0 就标记为“确认推荐” |
| 三天内回补是假突破 | D1～D3 任一天 `low <= gap_floor`，状态转为 `invalidated` |
| 三天不补为强势确认 | D1～D3 数据完整且每天 `low > gap_floor`，D3 收盘后转为 `confirmed` |
| 跳空力度越大，多头力量越强 | `gap_pct` 是排序分的一部分，但不能单独越过硬性过滤条件 |
| 持续、加速、衰竭型缺口 | 根据近 20 个交易日仍未完全回补的向上缺口数量做阶段风险标记 |
| 衰竭型缺口后“跑” | 第三个及以上未补缺口默认标为 `exhaustion_risk`，不进入默认确认推荐列表 |

## 5. 首个策略的确定性规格

### 5.1 名称与版本

- 策略 ID：`strong_gap_up_v1`
- 中文名：强势向上跳空缺口
- 类型：日线、收盘后、规则型事件策略
- 市场：沪深 A 股，股票池固定为沪深 300
- 配置必须带版本；结果必须记录实际使用的完整配置快照和配置哈希。

### 5.2 输入数据

每只股票至少需要：

- 未复权日线：`symbol, date, open, high, low, close, volume, amount`；
- 前复权日线：用于前置趋势、平台和回测收益连续性；
- 沪深 300 成分股快照；
- 股票名称、交易所、上市日期、证券状态快照；
- 沪深交易日历。

价格缺口几何必须以未复权价格计算，避免复权重写历史价格后制造或抹去真实缺口。前置趋势可以使用前复权收盘价。所有数据必须带 `as_of` 或有效日期。

### 5.3 D0 前资格过滤

以下条件全部满足才进入策略计算：

- 股票在 D0 对应的沪深 300 成分股集合内；
- 交易所为上海或深圳，代码不能是北交所证券；
- D0 的名称/状态不是 ST、`*ST`、退市或退市整理；
- `D0 - listing_date >= 60` 个自然日；
- D0 是市场交易日，并且该股票存在 D0 日线，`volume > 0`，OHLC 非空；
- 至少拥有 40 个 D0 之前的有效交易日日线；
- D-1 必须是交易日历定义的上一个交易日，不能按自然日 `date - 1` 推导。

任何资格数据缺失均应 fail closed：不推荐，并记录结构化排除原因，不能猜测或补造。

### 5.4 默认可配置参数

截图没有给出精确阈值，首版采用以下工程默认值。所有值都进入策略配置，后续只能通过新配置版本或回测结论调整，不能散落为代码魔法数字。

| 参数 | 默认值 | 含义 |
|---|---:|---|
| `minimum_listing_days` | 60 | 上市最少自然日 |
| `rise_window_days` | 20 | 平台前上涨观察窗口 |
| `platform_window_days` | 10 | D0 前横盘平台窗口 |
| `minimum_rise_return` | 10% | 上涨窗口前复权收盘收益下限 |
| `maximum_platform_amplitude` | 12% | 平台 `max(high)/min(low)-1` 上限 |
| `maximum_platform_drift` | 8% | 平台首尾前复权收盘偏移绝对值上限 |
| `minimum_gap_pct` | 1% | `(D0.low / D-1.high) - 1` 下限 |
| `volume_lookback_days` | 20 | 爆量基准窗口 |
| `minimum_volume_ratio` | 2.0 | D0 成交量 / 过去 20 日成交量中位数 |
| `minimum_close_location` | 0.60 | `(close-low)/(high-low)` 下限 |
| `confirmation_days` | 3 | D1～D3 完整回补观察窗口 |
| `gap_history_days` | 20 | 缺口阶段识别回看窗口 |
| `exhaustion_gap_count` | 3 | 第三个未补缺口起视为衰竭风险 |
| `max_entry_wait_days` | 3 | 确认后等待可交易开盘的最长交易日数 |
| `backtest_horizons` | 1, 3, 5, 10 | 入场后的事件收益观察期 |

### 5.5 D0 硬性触发条件

先定义：

- `gap_floor = raw_high[D-1]`
- `gap_ceiling = raw_low[D0]`
- `gap_pct = gap_ceiling / gap_floor - 1`
- `volume_ratio = volume[D0] / median(volume[D-20:D-1])`
- `close_location = (close[D0]-low[D0])/(high[D0]-low[D0])`

当 D0 为一字线导致 `high == low` 时，`close_location` 记为 1，但同时添加 `one_price_limit_risk`，后续入场必须执行可成交性判断。
由于合法一字线必然 `open == close`，它作为阳线条件的唯一例外；仍须满足实体向上缺口、放量、平台突破等其他全部硬条件，并且回测不会据此假定可以买入。

D0 触发条件全部为真：

1. `raw_low[D0] > raw_high[D-1]`；
2. `gap_pct >= minimum_gap_pct`；
3. `volume_ratio >= minimum_volume_ratio`；
4. D0 为阳线：`close[D0] > open[D0]`；
5. `close_location >= minimum_close_location`；
6. D0 收盘价突破前 10 个交易日平台最高价；
7. 平台前 20 个交易日的前复权收盘收益不低于 10%；
8. 平台振幅不高于 12%，平台首尾漂移绝对值不高于 8%。

满足后只生成 `triggered`/`watching` 信号，不能直接生成 `confirmed` 推荐。

### 5.6 D1～D3 生命周期

```text
D0 收盘发现缺口
    -> D1 收盘：观察承接
    -> D2 收盘：验证支撑
    -> D3 收盘：确认方向
        -> 任一天 low <= gap_floor：invalidated
        -> 三天均 low > gap_floor：confirmed
        -> 缺日线、停牌或数据不完整：indeterminate
```

补充状态：

- `partially_filled`：观察日最低价低于 `gap_ceiling`、但仍高于 `gap_floor`。它不是完整回补，信号继续观察，但必须暴露风险标记和剩余缺口比例。
- `invalidated`：D1～D3 任一天完整回补，记录首次回补日期；状态不可再恢复为确认。
- `confirmed`：D3 收盘后才能产生。三天必须是市场交易日历连续的三个交易日，且三天日线均完整。
- `indeterminate`：观察窗内出现停牌、缺失日线或数据质量失败。不能把“没有低价数据”误判为“没有回补”。
- `expired`：确认后超过前端展示有效期或超过可入场等待期。

### 5.7 缺口阶段与风险

在 D0 前 20 个交易日内统计尚未完整回补的向上缺口：

- 之前为 0 个：`persistent_candidate`，对应首个持续/突破型候选；
- 之前为 1 个：`accelerating_candidate`，增加 `late_trend_risk`；
- 之前不少于 2 个：`exhaustion_risk`，即当前是第三个或更后的未补缺口。

`exhaustion_risk` 结果仍写入审计数据，但默认 API 的 `confirmed` 推荐列表排除它；前端显式传入风险过滤条件后才可查看。该阶段只是基于当时可见数据的规则标签，不能使用之后价格倒推 D0 分类。

### 5.8 解释性排序分

多个候选只使用确定性规则分排序，不表示上涨概率。建议默认权重：

- 缺口宽度 30%；
- 成交量倍数 25%；
- 收盘位置 20%；
- 前置涨幅 15%；
- 平台紧凑度 10%。

各分量先按配置上限裁剪到 `[0, 1]` 再加权，最终输出 `rule_score` 0～100、每个分量的原始值和得分。不能调用模型或以 `confidence/probability` 命名该分数。

## 6. T+1 与时间语义

策略每天收盘后才拥有完整 D0 成交量和最低价，因此：

- D0 只进入观察池，不允许假设在 D0 收盘前已经知道完整条件；
- D3 收盘完成确认；
- 最早计划入场日是 D4；
- 若模拟在 D4 开盘买入，最早可卖出日是 D5；
- 所有 D1/D2/D3/D4/D5 均由 `CalendarService` 推导；
- 周末、法定休市日不占观察天数；个股停牌不会被当成“未回补”。

API 时间统一用 ISO 8601，业务时区固定 `Asia/Shanghai`。响应必须同时包含 `as_of_trade_date`、`generated_at` 和 `data_last_updated_at`，避免前端把旧数据当成实时数据。

## 7. 回测定义

### 7.1 回测类型

首版使用事件驱动回测，不构建资金组合、不优化仓位：

1. 逐交易日按当时可见数据生成 D0 信号；
2. 只用之后 D1～D3 数据更新生命周期；
3. D3 收盘确认后，选择 D4 或之后最多 3 个交易日内的首个可成交开盘作为模拟入场；
4. 若一直停牌或一字涨停无法成交，记为 `unfilled_entry`，不能按开盘价虚构成交；
5. 从实际入场日起计算 1、3、5、10 个交易日的收益，最短退出日必须满足 T+1；
6. 若持有期内完整回补缺口，在发现回补后的下一个可交易日开盘退出；若入场当日回补，也只能按 T+1 在次一交易日退出；
7. 同时保留固定期限事件收益，便于判断策略有效期，不用一个任意止盈规则掩盖结果。

默认成本必须配置化并在报告中显示，建议初值：买入滑点 5bp、卖出滑点 5bp、佣金双边各 3bp、卖出印花税按运行配置提供，不能把会变化的税费永远硬编码在策略模块。

### 7.2 回测输出

- 扫描日期范围、策略版本、配置哈希、数据快照时间；
- 候选数、确认数、回补失效数、数据不确定数、无法成交数；
- 各持有期样本数、平均/中位收益、胜率、收益分位数；
- 触发后 D1/D2/D3 的回补率；
- 按 `persistent/accelerating/exhaustion` 分组结果；
- 扣费前、扣费后结果；
- 使用的股票池模式和幸存者偏差提示；
- 逐事件明细，能够从汇总追溯到输入日线。

### 7.3 防前视与股票池质量

- D0 计算不得读取 D1 之后任何列；
- D3 确认不得在 D3 收盘前成为可用信号；
- 入场不得使用确认日 D3 的开盘或收盘价；
- 历史回测优先使用带有效日期的沪深 300 历史成分和证券状态快照；
- 从 `stock-analysis` 复制的 `UniverseService` 首次只有当前成分快照。若历史有效期数据尚未建立，允许开发验证回测，但响应必须标记 `universe_mode=current_snapshot`、`survivorship_bias=true`，不得宣称为正式历史验证；
- 正式回测发布门槛是 `universe_mode=point_in_time`。

## 8. 项目架构

建议目录如下，后续策略只在 `strategies/<strategy_id>/` 增加实现并注册：

```text
startkit/
  pyproject.toml
  README.md
  .env.example
  src/stock_strategy_api/
    main.py
    api/
      dependencies.py
      errors.py
      v1/
        health.py
        strategies.py
        recommendations.py
        stocks.py
        backtests.py
    core/
      config.py
      clock.py
      logging.py
    market_data/
      symbols.py
      retry.py
      calendar.py
      universe.py
      security_master.py
      ohlcv.py
      schemas.py
      parquet_store.py
      paths.py
    strategies/
      base.py
      registry.py
      strong_gap_up_v1/
        manifest.py
        config.py
        detector.py
        lifecycle.py
        scoring.py
        schemas.py
        README.md
    services/
      data_sync.py
      scan_service.py
      recommendation_service.py
      backtest_service.py
    repositories/
      database.py
      signal_repository.py
      run_repository.py
    cli.py
  data/                  # gitignore，运行态数据
  tests/
    fixtures/
    unit/
    integration/
    api/
  docs/
    plan.md
    task.md
    api.md               # 开发阶段生成
```

核心边界：

- `market_data` 负责真实数据、日期和资格信息，不包含策略判断；
- 每个策略目录只依赖稳定的数据模型与策略基类；
- `services` 负责编排数据同步、扫描、生命周期推进和结果物化；
- `api` 只查询物化结果，不直接访问 AKShare；
- `repositories` 隔离持久化实现，首版可使用 SQLite 保存运行记录/信号，Parquet 保存日线和快照。

## 9. 从 stock-analysis 复制的范围

### 9.1 允许复制并裁剪

| 来源 | 新项目目标 | 处理要求 |
|---|---|---|
| `quant_platform/core/fetch.py` | `market_data/retry.py` | 保留重试/退避；改为项目日志，不吞掉最终错误 |
| `quant_platform/core/market.py` | `market_data/symbols.py` | 保留代码规范化；删除仅按周末判断交易日的 `last_trade_day` |
| `quant_platform/core/universe.py` | `market_data/universe.py` | 首期只留 CSI300 配置 |
| `quant_platform/ingest/universe_service.py` | `market_data/universe.py` | 保留成分抓取、有效日期和 fail-loudly 语义 |
| `quant_platform/ingest/calendar_service.py` | `market_data/calendar.py` | 保留 `prev/next_trading_day` 和交易日范围查询 |
| `quant_platform/ingest/ohlcv_collector.py` | `market_data/ohlcv.py` | 保留增量采集；扩展为 raw/qfq 分离存储 |
| `quant_platform/store/schemas.py` | `market_data/schemas.py` | 仅复制 OHLCV 所需 schema |
| `quant_platform/store/parquet_store.py` | `market_data/parquet_store.py` | 保留原子写和日期规范化 |
| `quant_platform/store/lake.py` | `market_data/paths.py` | 只保留本项目实际使用路径 |
| 对应的轻量单元测试 | `tests/unit/market_data/` | 更新 import，保持失败即报错和原子写保障 |

复制后必须：

- 全部 import 改为 `stock_strategy_api...`；
- 增加来源注释或迁移说明，便于以后同步修复；
- 删除未使用的量化平台术语和依赖；
- 运行 `rg '/Users/fuziqiang/Desktop/stock-analysis|sys.path|quant_platform'`，业务源码结果必须为空；
- 不复制 `models/data`，运行数据由新项目自行同步或通过显式一次性导入命令复制，不能在代码中引用旧目录。

### 9.2 明确禁止复制

- `quant_platform/features/**`
- `quant_platform/labels/**`
- `quant_platform/training/**`
- `quant_platform/evaluation/**`
- `quant_platform/selection/**`
- `models/production/**`
- 预测、bakeoff、MLflow、SHAP 和模型注册脚本
- `technical_indicators.py` 的整套指标；本策略所需滚动值直接在策略目录用 pandas 实现

## 10. 数据持久化

### 10.1 Parquet 数据

```text
data/
  calendar/trading_calendar.parquet
  universe/csi300/membership.parquet
  security_master/snapshots/<date>.parquet
  market/raw/ohlcv/<symbol>.parquet
  market/qfq/ohlcv/<symbol>.parquet
```

所有写入原子化。日线唯一键是 `(symbol, date, adjustment)`；读取后按日期升序并去重。原始价格和前复权价格不可混在同一无标识表中。

### 10.2 SQLite 结果

至少包含：

- `strategy_definitions`：策略 ID、版本、展示文案、配置和配置哈希；
- `scan_runs`：运行范围、数据时间、状态、成功/失败统计；
- `signals`：每个股票/信号日的指标、状态、原因和风险；
- `signal_transitions`：D0～D3 状态变更审计；
- `backtest_runs`：回测参数、股票池质量、状态；
- `backtest_events`：逐事件入场、退出、成本和收益；
- `backtest_metrics`：聚合指标。

数据库启用 WAL；唯一约束保证同一 `(strategy_id, strategy_version, signal_date, symbol, config_hash)` 幂等。重复执行必须更新同一运行或返回已存在结果，不能制造重复推荐。

## 11. 统一 API 设计

前缀固定 `/api/v1`，统一响应带 `data` 和 `meta`；错误带稳定的 `code/message/details/request_id`。列表接口需要分页和明确排序。

### 11.1 端点

| 方法与路径 | 用途 |
|---|---|
| `GET /healthz` | 进程存活，不承诺数据新鲜 |
| `GET /readyz` | 数据库、交易日历和最近一次数据同步是否可用 |
| `GET /api/v1/strategies` | 策略目录 |
| `GET /api/v1/strategies/{strategy_id}` | 策略说明、规则、参数、版本和风险声明 |
| `GET /api/v1/strategies/{strategy_id}/recommendations` | 某策略结果，支持 `as_of/state/risk/limit/offset` |
| `GET /api/v1/recommendations` | 面向未来多策略的统一推荐流 |
| `GET /api/v1/stocks/{symbol}/recommendations` | 某股票在各策略下的信号历史 |
| `GET /api/v1/backtests` | 回测运行列表 |
| `GET /api/v1/backtests/{run_id}` | 回测汇总 |
| `GET /api/v1/backtests/{run_id}/events` | 分页事件明细 |

长任务用 CLI：

- `sync-data --as-of YYYY-MM-DD`
- `scan --strategy strong_gap_up_v1 --as-of YYYY-MM-DD`
- `advance-signals --as-of YYYY-MM-DD`
- `backtest --strategy strong_gap_up_v1 --start ... --end ...`

### 11.2 推荐字段

推荐 DTO 至少包含：

- 标识：`strategy_id, strategy_version, symbol, stock_name`；
- 日期：`signal_date, confirmation_date, as_of_trade_date, earliest_entry_date`；
- 状态：`state, phase, eligible, exclusion_reasons, risk_flags`；
- 缺口：`gap_floor, gap_ceiling, gap_pct, remaining_gap_pct`；
- 量价：`open, high, low, close, volume, amount, volume_ratio, close_location`；
- 前置形态：`rise_return, platform_amplitude, platform_drift`；
- 解释：`rule_score, score_components, reasons`；
- 数据质量：`calendar_source, universe_mode, survivorship_bias, data_last_updated_at`；
- 声明：`recommendation_kind=rule_based_observation` 和风险提示。

默认推荐接口只返回 `confirmed` 且非 `exhaustion_risk` 的结果。`watching` 与失效记录需要前端显式过滤查看。

## 12. 运行流程

每日收盘后按以下顺序执行：

1. 更新交易日历；
2. 更新 CSI300 成分和证券主数据快照；
3. 过滤当天无资格证券；
4. 增量更新 raw/qfq 日线，并生成数据质量摘要；
5. 先推进历史 D0 信号到 D1/D2/D3 状态；
6. 再扫描当天新 D0 信号；
7. 在一个数据库事务中物化结果和运行统计；
8. 就绪检查仅在最新预期交易日数据完整度达到门槛后通过。

任何上游关键步骤失败时，本次扫描标记 `failed`，保留上一成功结果但 API 必须在 `meta.stale=true` 中明确陈旧，不能返回空列表伪装为“没有股票”。

## 13. 测试与验收策略

### 13.1 单元测试

- 交易日跨周末、春节等长休市期的 D1～D5 推导；
- `low == gap_floor` 算完整回补；
- 部分回补不算完整回补；
- D1/D2/D3 任一缺数据得到 `indeterminate`；
- ST、退市、上市 59 日、北交所、停牌均被排除；上市 60 日边界允许；
- 平台、上涨、爆量、缺口、阳线、收盘位置各自的边界值；
- 第 1/2/3 个未补缺口的阶段分类；
- 同一数据和配置重复运行得到完全相同结果；
- D0 检测函数传入 D1 后数据时也只能截断到 `as_of=D0`。

### 13.2 API 契约测试

- OpenAPI 可生成；策略目录和详情字段稳定；
- 默认只返回确认且非衰竭风险的推荐；
- 未知策略、非法日期、非法股票代码、分页越界返回稳定错误码；
- 响应包含数据时间和陈旧标记；
- 空结果与数据失败可区分。

### 13.3 回测测试

- 构造日线验证 D0→D3→D4，任何成交不早于信息可用时间；
- D4 买入后不允许 D4 卖出；
- 回补退出使用下一可交易日开盘，避免日线内顺序假设；
- 一字涨停/停牌不能虚构入场；
- 成本计算与毛收益、净收益可逐笔对账；
- 当前成分快照回测必须输出幸存者偏差警告。

### 13.4 集成验收

使用固定的小型 Parquet fixture 离线跑完：数据读取、扫描、三日推进、结果入库、API 查询和回测。网络采集测试单独标记，不作为默认单测的前提。

## 14. 完成标准

只有同时满足以下条件才可称首期完成：

- 项目可在不访问 `stock-analysis` 路径的环境中安装、启动和测试；
- 只安装轻量策略服务所需依赖，不出现 MLflow/LightGBM/SHAP 等依赖；
- CSI300、资格过滤、raw/qfq 日线和交易日历均有明确质量状态；
- D0～D3 生命周期、T+1 回测和阶段风险均通过边界测试；
- API 文档包含真实样例和字段语义，前端无需理解内部 DataFrame；
- 回测无前视成交，并明确展示成本和幸存者偏差；
- `rg '/Users/fuziqiang/Desktop/stock-analysis|sys.path|quant_platform' src tests` 无结果；
- 所有单元、集成和 API 契约测试通过。

## 15. 实施阶段的默认决策

以下事项不再阻塞开发：

- 股票池固定 CSI300；
- 缺口观察窗口固定 D1～D3；
- 参数采用第 5.4 节默认值，之后通过策略配置版本调整；
- `exhaustion_risk` 默认不作为前端确认推荐；
- 首版采用 Parquet 存市场数据、SQLite 存运行结果；
- 日常推荐使用实时/当日快照，历史回测若缺 PIT 成分数据必须显式降级标记。
