# HTTP API 契约

服务只读取已物化的策略与回测结果。数据同步、扫描和回测由 CLI 触发。

## 通用约定

- API 前缀：`/api/v1`
- 业务时区：`Asia/Shanghai`
- 成功响应：`{"data": ..., "meta": ...}`
- 错误响应：`{"code", "message", "details", "request_id"}`
- 默认查询中，`meta.stale=true` 表示没有可证明为当前的数据，不能解释为“今天没有推荐”；显式历史 `as_of` 查询按指定交易日解释。
- 默认 `actionable` 推荐包含D2正常资格 `entry_eligible` 和D3独立延续资格 `continuation_entry`，并排除 `exhaustion_risk`。

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
| `state` | `actionable` | `actionable`、单个生命周期状态，或 `all` |
| `phase` | 空 | `persistent_candidate`, `accelerating_candidate`, `exhaustion_risk` |
| `risk` | `exclude_exhaustion` | 传 `include_exhaustion` 显式查看衰竭风险 |
| `version_scope` | `current` | 默认只查注册表当前版本；`all` 显式追溯旧版本 |
| `as_of` | 最近成功扫描 | ISO 交易日期 |
| `limit` | 50 | 1～200 |
| `offset` | 0 | 分页偏移 |

推荐记录包含策略/股票标识、D0/D1/D2/D3日期、SHORT/STRICT标签、D1承接类型、状态、阶段、冻结缺口边界、D0+D1排序分量、风险标记及数据质量。`structure_validity` 与 `entry_validity` 独立；`entry_kind` 区分 `normal_d2`、`execution_rollover` 和 `continuation_d3`。

示例结构：

```json
{
  "data": [
    {
      "strategy_id": "strong_gap_up_v1",
      "strategy_version": "2.1.0",
      "symbol": "600000",
      "stock_name": "浦发银行",
      "signal_date": "2026-06-30",
      "confirmation_date": "2026-07-01",
      "earliest_entry_date": "2026-07-02",
      "entry_eligible_until": "2026-07-02",
      "state": "entry_eligible",
      "structure_validity": true,
      "entry_validity": true,
      "entry_kind": "normal_d2",
      "entry_invalid_reason": null,
      "candidate_tags": ["SHORT_GAP", "STRICT_GAP"],
      "d1_confirmation": "fully_unfilled",
      "phase": "persistent_candidate",
      "gap_floor": 10.6,
      "gap_top": 10.8,
      "gap_pct": 0.018868,
      "volume_ratio": 3.0,
      "rule_score": 71.2,
      "recommendation_kind": "rule_based_observation",
      "risk_disclosure": "规则筛选结果仅供研究观察，不构成投资建议或收益承诺。",
      "as_of_trade_date": "2026-07-01"
    }
  ],
  "meta": {
    "as_of_trade_date": "2026-07-01",
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

返回某只股票的全部策略状态历史，包含 D0 待确认、D1弱承接、交易资格、失效、过期和衰竭风险。

D2正常窗口结束后，记录还会暴露：

- `normal_entry_window_closed_date`：仅表示普通D2入场窗口结束；
- `continuation_watch_date`、`continuation_entry_date`：D2重新评价及D3延续日期；
- `d2_close_location`、`d2_expansion_from_d0_close`：D3资格所用原始分量；
- `entry_invalid_reason=overextended`：扩张达到10%，只关闭新开仓资格；
- `structure_validity=false`：仅在原始缺口完整回补等结构性失效时出现。

## 回测

### `GET /api/v1/backtests`

分页返回回测运行；每项明确包含股票池模式、幸存者偏差、证券主数据 PIT 状态和 `production_verified`。

### `GET /api/v1/backtests/{run_id}`

返回配置、成本、样本数、固定1/2/3/4/5交易日事件收益、MFE、MAE、缺口回补率、SHORT/STRICT、D1承接和 `entry_kind_metrics` 分组结果，并报告D2/D3配对数量。
`production_verified` 只有在股票池和证券状态均为完整 PIT 数据时才可能为 `true`；同时返回 PIT 日期覆盖率。

### `GET /api/v1/backtests/{run_id}/events`

分页返回逐事件的信号日、确认日、真实入场日、退出日、毛/净收益、费用和状态轨迹。同一信号的D2 early与D3 continuation事件共享 `comparison_pair_id`，以 `entry_kind` 作为独立唯一维度，不会互相覆盖。

HTTP API 不提供创建回测的 POST 端点。
