from stock_strategy_api.strategies.base import StrategyMetadata

METADATA = StrategyMetadata(
    strategy_id="strong_gap_up_v1",
    version="1.0.0",
    name="强势向上跳空缺口",
    description="一轮上涨和横盘平台后放量向上跳空，随后三个交易日不完全回补才确认。",
    risk_disclosure="历史规则结果不代表未来收益；服务输出仅为规则型观察信息。",
)
