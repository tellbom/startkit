from stock_strategy_api.strategies.base import StrategyMetadata

METADATA = StrategyMetadata(
    strategy_id="strong_gap_up_v1",
    version="2.1.0",
    name="强势向上跳空缺口",
    description="强势结构中放量向上跳空，D1验证承接，D2正常入场，并独立裁决D3延续入场。",
    risk_disclosure="历史规则结果不代表未来收益；服务输出仅为规则型观察信息。",
)
