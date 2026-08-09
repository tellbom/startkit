# 股票策略集 FastAPI 服务任务清单

> 配套设计：`docs/plan.md`
> 目标：供后续 Agent 直接领取和实施
> 原则：每项任务都必须有可运行验证，不以“代码已写”代替验收

## 1. 执行规则

### 1.1 任务编号

- `FND`：项目基础设施
- `DAT`：市场数据与迁移
- `STR`：策略实现
- `RUN`：扫描、状态与持久化
- `API`：FastAPI 契约
- `BT`：规则回测
- `QA`：质量、文档和发布门槛

### 1.2 所有 Agent 的硬约束

1. 先阅读 `docs/plan.md`，不得改变已确认的股票池、D1～D3 回补定义或 T+1 语义。
2. 每个策略必须是独立目录；首个策略只能写入 `strategies/strong_gap_up_v1/` 及公共策略接口。
3. 需要复用的 `stock-analysis` 代码必须复制并裁剪到本项目。禁止绝对路径 import、`sys.path` 注入、软链接和本地路径依赖。
4. 禁止复制机器学习、量化特征/标签、训练、评估、选股模型和模型产物。
5. 网络失败、数据缺失和停牌必须显式失败或标记不确定，禁止生成假数据。
6. 不修改 `temp/` 截图；它们是需求证据。
7. 每个任务只修改其“文件所有权”范围；公共契约需要先落地，再由其他任务依赖。
8. 测试结论必须区分：离线 fixture 验证、真实网络采集验证、正式历史股票池验证。

### 1.3 建议 Agent 分工

| 工作流 | 可领取任务 | 主要文件所有权 |
|---|---|---|
| Agent A：基础与数据 | FND-01、DAT-01～DAT-05 | `core/`, `market_data/`, 数据类测试 |
| Agent B：策略 | STR-01～STR-04 | `strategies/`, 策略单测 |
| Agent C：服务与 API | RUN-01～RUN-03、API-01～API-03 | `services/`, `repositories/`, `api/` |
| Agent D：回测与质量 | BT-01～BT-03、QA-01～QA-03 | 回测模块、跨层测试、文档 |

同一时间不要让两个 Agent 编辑同一个公共文件。`FND-01`、`STR-01`、`RUN-01` 是主要契约检查点，合入后再并行实现。

## 2. 阶段与依赖

```text
FND-01
  ├─ DAT-01 ─ DAT-02 ─┬─ DAT-03
  │                   ├─ DAT-04 ─ DAT-05
  │                   └─ STR-02
  ├─ STR-01 ─ STR-02 ─ STR-03 ─ STR-04
  └─ RUN-01 ─ RUN-02 ─ RUN-03 ─ API-01 ─ API-02 ─ API-03
                         └──────── BT-01 ─ BT-02 ─ BT-03

全部功能 -> QA-01 -> QA-02 -> QA-03
```

## 3. 基础任务

### FND-01：初始化可安装的 FastAPI 项目

| 字段 | 内容 |
|---|---|
| 优先级 | P0，所有任务前置 |
| 依赖 | 无 |
| 文件所有权 | `pyproject.toml`, `.env.example`, `.gitignore`, `README.md`, `src/stock_strategy_api/core/**`, `src/stock_strategy_api/main.py`, 基础测试配置 |

目标：建立最小、可安装、可测试的 `src` 布局，不加入业务规则。

实现要求：

- Python 版本在 `pyproject.toml` 固定下限；依赖仅包含 FastAPI 服务、Pydantic 配置、pandas/pyarrow、AKShare、交易日历、SQLite 所需组件和测试工具。
- 不得包含 LightGBM、XGBoost、CatBoost、SHAP、MLflow、Optuna、DVC 或 scikit-learn。
- 实现应用工厂、统一配置、日志、`Asia/Shanghai` 业务时区和 request ID 中间件。
- 配置项覆盖数据目录、数据库 URL、策略配置、数据新鲜度阈值和回测成本；提交 `.env.example`，不提交秘密。
- `data/`、数据库、缓存、日志和 Parquet 运行文件加入 `.gitignore`。

验收：

- 全新虚拟环境可安装项目；
- `python -c "import stock_strategy_api"` 成功；
- TestClient 可启动应用；
- 依赖清单中不存在被禁止的机器学习框架；
- 此任务结束时除 health 骨架外不应存在策略判断。

## 4. 数据迁移与市场数据

### DAT-01：裁剪复制公共数据代码

| 字段 | 内容 |
|---|---|
| 优先级 | P0 |
| 依赖 | FND-01 |
| 来源 | `/Users/fuziqiang/Desktop/stock-analysis/quant_platform/` |
| 文件所有权 | `src/stock_strategy_api/market_data/{symbols,retry,schemas,parquet_store,paths}.py`, 对应单测 |

复制并改造：

- `core/fetch.py` 的重试退避；
- `core/market.py` 的证券代码规范化；
- `store/schemas.py` 的 OHLCV 子集；
- `store/parquet_store.py` 的读取和原子写；
- `store/lake.py` 中本项目需要的路径函数。

必须删除：

- `quant_platform` import；
- 默认 `/mnt/project`、旧项目路径和旧数据目录；
- 与估值、资金流、特征、标签、模型相关的 schema/path；
- 仅按工作日推断交易日的 `last_trade_day`。

验收：

- 复制后的代码可以单独 import；
- Parquet 写入中断不会破坏已有文件；
- OHLCV 缺必需列时明确报错；
- `rg '/Users/fuziqiang/Desktop/stock-analysis|sys.path|quant_platform' src/stock_strategy_api/market_data tests` 无结果。

### DAT-02：迁移交易日历服务

| 字段 | 内容 |
|---|---|
| 优先级 | P0 |
| 依赖 | DAT-01 |
| 来源 | `stock-analysis/quant_platform/ingest/calendar_service.py` |
| 文件所有权 | `market_data/calendar.py`, `tests/unit/market_data/test_calendar.py` |

实现要求：

- 复制并改造交易日历缓存、`prev_trading_day`、`next_trading_day`、区间查询和来源信息；
- 优先使用权威数据源，离线可回退到 `exchange_calendars`；响应必须暴露具体来源和准确性提示；
- 增加 `nth_trading_day_after(date, n)`，统一计算 D1～D5；
- 请求超出日历覆盖范围时失败，不循环猜日期；
- 日期均为 `date`，时间戳均带 `Asia/Shanghai` 时区。

验收：

- 周五后的 D1 在正常周为下周一；
- 跨长假 fixture 的 D1/D2/D3 正确；
- `nth_trading_day_after(D0, 0)` 行为有明确约定和测试；
- 范围不足时抛出稳定领域错误。

### DAT-03：迁移 CSI300 股票池服务

| 字段 | 内容 |
|---|---|
| 优先级 | P0 |
| 依赖 | DAT-01、DAT-02 |
| 来源 | `core/universe.py`, `ingest/universe_service.py` |
| 文件所有权 | `market_data/universe.py`, 股票池 fixture 和单测 |

实现要求：

- 只保留 `csi300`，未知股票池不得静默回退；
- 复制当前成分抓取、成员新增/移出有效期记录和原子持久化；
- `members_as_of(date)` 必须按 `in_date <= date <= out_date/null` 查询；
- 每个结果携带 `universe_mode=current_snapshot|point_in_time` 和 `survivorship_bias`；
- 第一次同步的当前成分快照不能伪装为历史成分；
- 代码层再次拒绝北交所代码，即使上游异常返回。

验收：

- fixture 中成分进入/移出边界正确；
- 首次当前快照用于更早日期时会返回质量警告或按配置失败；
- 当前成分同步失败时保留旧数据但标记陈旧，不写空表覆盖；
- 所有返回代码为规范化 6 位字符串。

### DAT-04：证券主数据与资格过滤

| 字段 | 内容 |
|---|---|
| 优先级 | P0 |
| 依赖 | DAT-02、DAT-03 |
| 文件所有权 | `market_data/security_master.py`, 主数据 schema/path, 资格测试 |

实现要求：

- 定义 `SecuritySnapshot`：`symbol, name, exchange, listing_date, status, effective_date, source`；
- 每日缓存快照，不允许 API 请求逐只抓取；
- 优先按结构化状态排除 ST/退市整理；名称包含 `ST`、`*ST`、`退` 仅作为防御性兜底；
- 上市天数按自然日计算，`>=60` 才合格；
- 北交所始终排除；
- 当日停牌用“当日存在有效日线且 `volume > 0`”与状态信息共同判断；
- 缺 listing date、状态或当天交易数据时 fail closed，并写具体 `exclusion_reason`。

验收边界：

- 上市 59 日排除、60 日允许；
- 普通名称中非状态含义的字母组合不会误判；
- `*ST`、退市整理、北交所、无 D0 bar、D0 volume=0 全部排除；
- 历史回测使用当前状态时必须带 `security_master_pit=false` 警告。

### DAT-05：raw/qfq 日线增量采集

| 字段 | 内容 |
|---|---|
| 优先级 | P0 |
| 依赖 | DAT-01、DAT-03、DAT-04 |
| 来源 | `ingest/ohlcv_collector.py` |
| 文件所有权 | `market_data/ohlcv.py`, OHLCV fixture 和采集单测 |

实现要求：

- 将旧 collector 复制到项目内，保留串行默认、重试、增量合并、去重和原子写；
- 同一批次分别采集未复权 `raw` 和前复权 `qfq`，路径强制分开；
- 采集范围只来自 CSI300 服务，不接受接口调用方任意扩大到全市场；
- 单只失败不写假数据，运行摘要记录成功、失败、跳过、最后日期；
- 默认低并发，避免上游封禁；
- D0 结束后检查每只合格股票是否真的有 D0 行。

验收：

- 第二次运行只抓增量；
- raw/qfq 文件不会互相覆盖；
- 同一 `(symbol,date)` 不重复；
- 网络全失败时旧文件保持不变；
- 固定 fixture 的 raw 缺口不会受 qfq 数据替换影响。

## 5. 策略任务

### STR-01：定义公共策略协议和注册表

| 字段 | 内容 |
|---|---|
| 优先级 | P0，策略/API 共同契约 |
| 依赖 | FND-01 |
| 文件所有权 | `strategies/base.py`, `strategies/registry.py`, 公共策略 DTO 测试 |

协议至少定义：

- `metadata()`：ID、版本、名称、说明、风险声明；
- `config_schema()` 与配置快照/哈希；
- `detect(history, as_of, eligibility)`：只检测 D0；
- `advance(signal, bars, calendar, as_of)`：推进 D1～D3；
- `explain(signal)`：结构化原因和得分分量；
- 策略输出 DTO，不能直接向 API 泄露 DataFrame。

注册表要求：

- ID 唯一，重复注册启动失败；
- 未知策略返回领域错误；
- API 和 CLI 均从注册表发现策略，禁止各自维护策略列表；
- 单个策略 import 失败时就绪检查失败，不能悄悄缺少策略。

验收：使用两个最小假策略证明统一发现、配置校验、重复 ID 和未知 ID 行为。

### STR-02：实现强势缺口 D0 检测器

| 字段 | 内容 |
|---|---|
| 优先级 | P0 |
| 依赖 | STR-01、DAT-02、DAT-05 |
| 文件所有权 | `strategies/strong_gap_up_v1/{manifest,config,detector,schemas}.py`, `README.md`, 检测器单测 |

严格实现 `plan.md` 第 5.3～5.5 节：

- 使用 raw 计算缺口和平台价格边界；
- 使用 qfq 计算前置上涨收益和平台漂移；
- 过去窗口必须按交易日排序和切片；
- 爆量分母使用前 20 日成交量中位数，不包含 D0；
- `high == low` 时处理一字线并加风险标记；
- 每个硬条件都输出 pass/fail、实际值、阈值和排除原因；
- 输入先截断到 `as_of`，即使调用方意外提供未来数据也不能读取。

必须覆盖的测试：

- 纯合格样例；
- `low == D-1.high` 不形成缺口；
- 0.99% 与 1.00% 阈值；
- D0 成交量错误地进入历史中位数的回归测试；
- 没有前置涨幅、平台过宽、阴线、收盘位置过低分别失败；
- 数据不足失败且理由稳定；
- qfq 与 raw 数值不同但缺口仍由 raw 决定。

### STR-03：实现 D1～D3 生命周期

| 字段 | 内容 |
|---|---|
| 优先级 | P0 |
| 依赖 | STR-02、DAT-02 |
| 文件所有权 | `strong_gap_up_v1/lifecycle.py`, 生命周期单测 |

实现状态：`triggered`, `watching_d1`, `watching_d2`, `partially_filled`, `confirmed`, `invalidated`, `indeterminate`, `expired`。

规则：

- 用交易日历计算 D1/D2/D3；
- 任一天 `low <= gap_floor` 后永久 `invalidated`；
- `gap_floor < low < gap_ceiling` 是部分回补，记录剩余缺口比例；
- 三天完整且均未完全回补，只能在 D3 收盘后 `confirmed`；
- 任一天缺数据或停牌转为 `indeterminate`，不延长观察窗、不自动确认；
- 重复推进相同日期必须幂等；状态不能倒退。

验收测试包括周末/长假、D1/D2/D3 各自回补、等价边界、部分回补、停牌、缺数据、重复推进和非法倒退。

### STR-04：实现阶段风险和规则排序

| 字段 | 内容 |
|---|---|
| 优先级 | P1 |
| 依赖 | STR-02、STR-03 |
| 文件所有权 | `strong_gap_up_v1/scoring.py`, 排序/阶段单测 |

实现要求：

- 在 D0 前 20 个交易日统计尚未完整回补的向上缺口；
- 0/1/2+ 分别标记 `persistent_candidate`、`accelerating_candidate`、`exhaustion_risk`；
- 只用 D0 当时可见历史，不允许事后分类；
- 按计划权重输出 0～100 的 `rule_score` 和每个分量；
- 分母为零、异常量或极端 gap 先按明确规则处理再裁剪；
- 相同分数以 `signal_date desc, symbol asc` 稳定排序；
- 字段命名不能使用 `probability` 或暗示统计置信度。

验收：第三个缺口默认被风险过滤；改变配置版本能改变排序但不覆盖旧结果；相同输入产生相同分数。

## 6. 运行编排和存储

### RUN-01：建立结果数据库与 repository

| 字段 | 内容 |
|---|---|
| 优先级 | P0 |
| 依赖 | FND-01、STR-01 |
| 文件所有权 | `repositories/**`, 数据库迁移/初始化代码和 repository 测试 |

按计划建立策略定义、扫描运行、信号、状态变更、回测运行、事件和指标表。

要求：

- 默认 SQLite + WAL，数据库路径来自配置；
- JSON 字段保存配置快照、规则明细和风险；
- 时间戳带时区，交易日期单独用 DATE；
- 信号自然唯一键包含策略版本和配置哈希；
- repository 提供事务边界、分页和稳定排序；
- 失败运行保留错误摘要，但不能提交半批信号。

验收：重复 upsert 不增行；状态变更有审计；扫描事务失败回滚；分页无重复/漏项。

### RUN-02：实现每日数据同步与扫描服务

| 字段 | 内容 |
|---|---|
| 优先级 | P0 |
| 依赖 | DAT-02～DAT-05、STR-02、RUN-01 |
| 文件所有权 | `services/data_sync.py`, `services/scan_service.py`, 相应集成测试 |

每日顺序固定：日历 → 股票池 → 主数据 → 日线 → 数据质量 → 推进旧信号 → 扫描新信号 → 原子物化。

要求：

- 每步写结构化运行统计；
- `as_of` 非交易日默认拒绝，只有显式查询最近结果时才允许回退；
- 上游失败时扫描 run 为 `failed`，不能写空成功结果；
- 每个策略独立失败隔离，但整体 ready 状态反映部分失败；
- 支持按同一日期幂等重跑；
- 并发运行用数据库锁/运行唯一键防重入。

验收：离线 fixture 可完整运行；中途注入异常时上一成功结果仍可读并标记 stale；重复扫描不重复信号。

### RUN-03：实现 CLI

| 字段 | 内容 |
|---|---|
| 优先级 | P1 |
| 依赖 | RUN-02、BT-01 |
| 文件所有权 | `cli.py`, CLI 测试和 README 命令章节 |

命令：

- `sync-data --as-of`
- `scan --strategy --as-of`
- `advance-signals --as-of`
- `backtest --strategy --start --end --config`
- `show-run --run-id`

要求：非零退出码区分配置、数据、策略和内部错误；输出 run ID 和摘要；长任务不由公开 HTTP GET 触发。

## 7. FastAPI 任务

### API-01：统一响应、错误与健康检查

| 字段 | 内容 |
|---|---|
| 优先级 | P0 |
| 依赖 | FND-01、RUN-01、RUN-02 |
| 文件所有权 | `api/dependencies.py`, `api/errors.py`, `api/v1/health.py`, API 基础测试 |

实现：

- `{data, meta}` 响应封装；
- `{code, message, details, request_id}` 错误；
- `/healthz` 只检查进程；
- `/readyz` 检查数据库、策略注册、日历覆盖和最新预期交易日数据；
- `meta` 含 request ID、生成时间、as-of、数据时间、stale 和质量警告。

验收：空结果、陈旧结果和服务错误三者响应可区分；错误不暴露堆栈和本地路径。

### API-02：策略和推荐读接口

| 字段 | 内容 |
|---|---|
| 优先级 | P0 |
| 依赖 | STR-04、RUN-02、API-01 |
| 文件所有权 | `api/v1/strategies.py`, `api/v1/recommendations.py`, `api/v1/stocks.py`, DTO 和契约测试 |

实现计划中定义的五个读接口：策略列表/详情、策略推荐、统一推荐流、股票推荐历史。

要求：

- 默认 `state=confirmed` 且排除 `exhaustion_risk`；
- `as_of` 是交易日语义，未给时返回最近成功扫描日并在 meta 明示；
- 支持状态、阶段、风险、股票代码、分页筛选；
- 推荐 DTO 包含完整原因、参数实际值、数据质量和最早入场日；
- 股票代码严格校验，排序稳定；
- 不直接返回 pandas/numpy 类型。

验收：OpenAPI schema 快照稳定；默认过滤、显式查看 watching/invalidated、分页和未知策略均有测试。

### API-03：回测只读接口与 API 文档

| 字段 | 内容 |
|---|---|
| 优先级 | P1 |
| 依赖 | BT-02、API-01 |
| 文件所有权 | `api/v1/backtests.py`, `docs/api.md`, 回测 API 测试 |

实现回测运行列表、汇总详情和事件分页。不通过公开接口创建回测。

验收：运行中/成功/失败状态区分；指标带样本数与成本假设；幸存者偏差和 PIT 状态不能从响应中省略；文档提供成功、空结果和错误示例。

## 8. 回测任务

### BT-01：实现无前视事件生成器

| 字段 | 内容 |
|---|---|
| 优先级 | P0 |
| 依赖 | DAT-02～DAT-05、STR-03 |
| 文件所有权 | `services/backtest_service.py` 的事件生成部分、回测 fixture 和单测 |

实现逐日 replay：

- D0 只看截至 D0 的数据；
- D3 收盘后才确认；
- 最早 D4 开盘入场；
- 最多等待 3 个交易日寻找可成交开盘；
- 停牌和一字涨停记为不可成交；
- 每个事件保存输入日期和状态轨迹。

验收：测试故意放入未来极值，D0 结果不得变化；任何入场时间严格晚于确认时间；不可成交样例不会出现在已成交收益样本中。

### BT-02：实现 T+1 退出、成本和指标

| 字段 | 内容 |
|---|---|
| 优先级 | P0 |
| 依赖 | BT-01、RUN-01 |
| 文件所有权 | `services/backtest_service.py` 的成交/指标部分、指标单测 |

实现要求：

- 入场日不能卖出；
- 入场后完整回补，在下一个可交易日开盘退出；
- 同时计算 1/3/5/10 交易日固定期限事件收益；
- 毛收益、滑点、佣金、印花税和净收益逐项保存；
- 输出样本数、均值、中位数、胜率、分位数、回补率及分阶段结果；
- 不实现资金组合、仓位优化或机器学习评价指标。

验收：手算 fixture 的每笔毛/净收益完全一致；D4 买入/D4 回补只能 D5 或更晚卖出；税费配置改变会生成新的配置哈希，不覆盖旧回测。

### BT-03：股票池/PIT 质量门禁

| 字段 | 内容 |
|---|---|
| 优先级 | P1 |
| 依赖 | BT-02、DAT-03、DAT-04 |
| 文件所有权 | 回测质量检查、质量测试、报告字段 |

实现要求：

- 每次回测记录 `universe_mode`、`survivorship_bias`、`security_master_pit`；
- 当前快照模式允许开发回放，但结果标题和 API 必须明确“存在幸存者偏差”；
- 正式模式要求每个回测日期存在 PIT 股票池和资格状态；缺一天则按配置失败或排除并列出覆盖率；
- 禁止默默用今天的股票名称/状态判断历史日期。

验收：当前快照回测无法被标记为 `production_verified`；PIT fixture 可通过正式门禁；覆盖缺口会在汇总与逐事件层都可追踪。

## 9. 质量与交付任务

### QA-01：跨层离线验收

| 字段 | 内容 |
|---|---|
| 优先级 | P0 |
| 依赖 | DAT、STR、RUN、API、BT 所有功能任务 |
| 文件所有权 | `tests/integration/**`, `tests/api/**`, 固定 fixture |

建立至少包含以下股票的离线数据集：

- 完整确认的第一个强势缺口；
- D1、D2、D3 分别完整回补；
- 部分回补但未完全回补；
- 第三个缺口衰竭风险；
- ST、上市 59 日、停牌、数据缺失；
- D4 一字涨停无法入场；
- 跨周末和长假。

一条测试链必须跑通：fixture 入库 → D0 扫描 → D1～D3 推进 → API 查询 → T+1 回测 → 汇总对账。

### QA-02：独立运行与范围审计

| 字段 | 内容 |
|---|---|
| 优先级 | P0 |
| 依赖 | QA-01 |
| 文件所有权 | 审计脚本或测试、README 部署章节 |

验收命令至少包括：

```bash
rg '/Users/fuziqiang/Desktop/stock-analysis|sys.path|quant_platform' src tests
rg 'lightgbm|xgboost|catboost|shap|mlflow|optuna|dvc' pyproject.toml src
pytest
```

前两个 `rg` 必须无业务代码命中。随后把 `startkit` 单独复制到临时目录，在旧项目路径不可见的条件下安装、跑测试、启动 API 并读取 fixture 推荐。

不得把旧项目 `models/data` 的存在作为通过条件。

### QA-03：真实数据 smoke test 与交接报告

| 字段 | 内容 |
|---|---|
| 优先级 | P1 |
| 依赖 | QA-02 |
| 文件所有权 | `docs/verification.md`, README 最终状态 |

在网络允许时执行一次真实 CSI300 同步和最近交易日扫描：

- 记录成分数量、各资格过滤数量、raw/qfq 成功率、失败股票和数据最新日期；
- 随机抽取至少 3 只股票人工核对 D-1/D0 OHLC、缺口和量比；
- 若没有候选，这是有效业务结果，但必须证明数据同步成功且规则确实运行；
- 网络失败不得用 fixture 结果冒充真实验证；
- 报告分别标明 `implemented`、`fixture_verified`、`network_verified`、`pit_backtest_verified`。

最终交接包含启动命令、同步/扫描/回测命令、API 示例、已知限制和下一策略接入方法。

## 10. 最终 Definition of Done

- [x] `docs/plan.md` 的所有硬约束均有对应代码与测试。
- [x] 首个策略完整位于独立目录并通过公共注册表暴露。
- [x] 运行时完全独立于 `stock-analysis` 路径。
- [x] raw/qfq 数据职责清晰，缺口使用 raw。
- [x] CSI300、ST/退市、上市 60 日、北交所和停牌过滤均可审计。
- [x] D0 观察、D1～D3 推进和三日确认没有未来数据泄漏。
- [x] T+1 入场/退出与交易日历边界通过测试。
- [x] `exhaustion_risk` 默认不进入确认推荐。
- [x] API 可区分无推荐、数据陈旧和运行失败。
- [x] 回测展示成本、样本数和股票池/PIT 质量。
- [x] 离线全链路测试通过。
- [x] 真实数据验证与 fixture 验证被明确区分。
