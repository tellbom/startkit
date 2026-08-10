from stock_strategy_api.strategies.base import StrategyMetadata

METADATA = StrategyMetadata(
    strategy_id="strong_gap_up_v1",
    version="2.0.0",
    name="强势向上跳空缺口",
    description="强势结构中放量向上跳空，D1验证缺口承接，D2最早参与未来1至3日短线惯性。",
    risk_disclosure="历史规则结果不代表未来收益；服务输出仅为规则型观察信息。",
)
