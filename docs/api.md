# HTTP API 契约

服务只读取已物化的策略与回测结果。数据同步、扫描和回测由 CLI 触发。

## 通用约定

- API 前缀：`/api/v1`
- 业务时区：`Asia/Shanghai`
- 成功响应：`{"data": ..., "meta": ...}`
- 错误响应：`{"code", "message", "details", "request_id"}`
- `meta.stale=true` 表示没有可证明为当前的数据，不能解释为“今天没有推荐”。
- 默认推荐仅包含 `confirmed`，并排除 `exhaustion_risk`。

## 健康检查

### `GET /healthz`

只表示进程存活。

### `GET /readyz`

检查数据库、策略注册、交易日历、最近成功扫描和最近预期交易日。数据未同步时返回 503。

## 策略

### `GET /api/v1/strategies`

返回策略目录、版本、规则说明、完整配置和配置哈希。

### `GET /api/v1/strategies/{strategy_id}`

返回单个策略详情。未知策略返回：

```json
{
  "code": "unknown_strategy",
  "message": "unknown strategy: missing",
  "details": {},
  "request_id": "..."
}
```

## 推荐

### `GET /api/v1/strategies/{strategy_id}/recommendations`

### `GET /api/v1/recommendations`

查询参数：

| 参数 | 默认值 | 含义 |
|---|---|---|
| `strategy_id` | 空 | 统一流可指定策略 |
| `state` | `confirmed` | 生命周期状态，或 `all` |
| `phase` | 空 | `persistent_candidate`, `accelerating_candidate`, `exhaustion_risk` |
| `risk` | `exclude_exhaustion` | 传 `include_exhaustion` 显式查看衰竭风险 |
| `as_of` | 最近成功扫描 | ISO 交易日期 |
| `limit` | 50 | 1～200 |
| `offset` | 0 | 分页偏移 |

推荐记录包含策略/股票标识、D0/D3/D4 日期、状态、阶段、缺口边界、量价指标、前置形态、规则检查、排序分量、风险标记及数据质量。

示例结构：

```json
{
  "data": [
    {
      "strategy_id": "strong_gap_up_v1",
      "strategy_version": "1.0.0",
      "symbol": "600000",
      "stock_name": "浦发银行",
      "signal_date": "2026-06-30",
      "confirmation_date": "2026-07-03",
      "earliest_entry_date": "2026-07-06",
      "state": "confirmed",
      "phase": "persistent_candidate",
      "gap_floor": 10.6,
      "gap_ceiling": 10.8,
      "gap_pct": 0.018868,
      "volume_ratio": 3.0,
      "rule_score": 71.2,
      "recommendation_kind": "rule_based_observation",
      "risk_disclosure": "规则筛选结果仅供研究观察，不构成投资建议或收益承诺。",
      "as_of_trade_date": "2026-07-03"
    }
  ],
  "meta": {
    "as_of_trade_date": "2026-07-03",
    "stale": false,
    "warnings": [],
    "total": 1,
    "limit": 50,
    "offset": 0
  }
}
```

无推荐仍返回成功响应，并通过 `meta.stale` 区分“已完成扫描但无候选”与“没有新鲜成功扫描”：

```json
{
  "data": [],
  "meta": {
    "as_of_trade_date": null,
    "data_last_updated_at": null,
    "stale": true,
    "warnings": ["No sufficiently fresh successful scan is available for this query."],
    "total": 0,
    "limit": 50,
    "offset": 0
  }
}
```

非法交易日期示例：

```json
{
  "code": "invalid_trade_date",
  "message": "2026-07-04 is not a trading day",
  "details": {},
  "request_id": "..."
}
```

### `GET /api/v1/stocks/{symbol}/recommendations`

返回某只股票的全部策略状态历史，包含观察、失效、确认和衰竭风险。

## 回测

### `GET /api/v1/backtests`

分页返回回测运行；每项明确包含股票池模式、幸存者偏差、证券主数据 PIT 状态和 `production_verified`。

### `GET /api/v1/backtests/{run_id}`

返回配置、成本、样本数、固定 1/3/5/10 交易日事件收益和主退出规则结果。
`production_verified` 只有在股票池和证券状态均为完整 PIT 数据时才可能为 `true`；同时返回 PIT 日期覆盖率。

### `GET /api/v1/backtests/{run_id}/events`

分页返回逐事件的信号日、确认日、真实入场日、退出日、毛/净收益、费用和状态轨迹。

HTTP API 不提供创建回测的 POST 端点。
