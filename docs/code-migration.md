# 数据代码迁移说明

本项目的数据层根据本地既有股票分析工程中的轻量模块裁剪迁移，目标是复用经过验证的数据规范与失败语义，同时保持本项目可独立安装和运行。

| 本项目模块 | 迁移参考模块 | 保留内容 |
|---|---|---|
| `market_data/retry.py` | `core/fetch.py` | 重试、退避、失败不造数 |
| `market_data/symbols.py` | `core/market.py` | A 股代码规范化与交易所识别 |
| `market_data/schemas.py` | `store/schemas.py` | OHLCV 必需列、类型与去重 |
| `market_data/parquet_store.py` | `store/parquet_store.py` | 原子写入、日期序列化 |
| `market_data/calendar.py` | `ingest/calendar_service.py` | 沪深交易日历和前后交易日查询 |
| `market_data/universe.py` | `core/universe.py`, `ingest/universe_service.py` | CSI300 成分有效期、失败显式化 |
| `market_data/ohlcv.py` | `ingest/ohlcv_collector.py` | 增量抓取、双数据源回退、raw/qfq 分离 |

迁移后做了以下隔离：

- 包名和 import 全部改成本项目内部路径；
- 删除模型、特征、标签、训练、评估、选股和模型产物依赖；
- 删除仅按周末猜交易日的逻辑；
- 不读取旧工程数据目录，不通过本地路径安装旧工程；
- 新增证券主数据资格过滤、策略生命周期和 FastAPI 物化查询。
