# 企业微信 Docker 部署交付说明

## 当前边界

- 当前部署仅位于 `fuziqiang@192.168.31.37`，与旧 A11 环境无关。
- 已实现企业微信智能机器人 WebSocket 主动推送，并以企业微信返回 `errcode=0` 作为成功标准。
- 当前 `scripts/push_wecom.py` 是一次性任务：连接、鉴权、发送后退出。它不会常驻接收群内 `@机器人` 消息；若要实现对话，需要另建常驻 WebSocket 消费服务，处理消息事件、回复和重连。
- 企业微信参考文档：<https://open.work.weixin.qq.com/help/wap/detail?docid=21661>

## 企业微信配置

| 配置 | 值 |
|---|---|
| Bot ID | `aibHLKYNswBMyVD-kN08wk_ByU0AcxbBTni` |
| Chat ID | `wr_oudbQAAsqiwKjruURDMbl96Qyx5bQ` |
| Secret | 仅存放在服务器环境文件的 `WECOM_BOT_SECRET`，禁止提交到 Git |
| WebSocket | `wss://openws.work.weixin.qq.com` |

服务器环境文件：`/Users/fuziqiang/.config/stock-strategy-api.env`，权限必须为 `600`。

## 部署结构

- 项目：`/Users/fuziqiang/stock-strategy-api`
- 持久数据：`/Users/fuziqiang/stock-strategy-api/data`
- 日志：`/Users/fuziqiang/stock-strategy-api/logs/daily-launchd.log`
- 镜像：`stock-strategy-api:20260817-incremental`
- LaunchAgent：`com.stock-strategy-api.daily`
- 计划：每周一至周五，Asia/Shanghai 16:00

`scripts/run_daily_macos.sh` 是手工和定时任务的统一入口。它使用固定 PATH 和 Docker 绝对路径；若 Docker daemon 未启动，会打开 Docker Desktop并等待最多 120 秒。容器只挂载持久化数据目录，密钥通过只读配置文件语义的 `--env-file` 注入，不写入镜像。

## 常用验收命令

```sh
launchctl print gui/$(id -u)/com.stock-strategy-api.daily
tail -f /Users/fuziqiang/stock-strategy-api/logs/daily-launchd.log

# 从真实定时入口立即触发
launchctl kickstart -k gui/$(id -u)/com.stock-strategy-api.daily

# 独立验证当天行情是否已经最新，不发送企业微信消息
docker run --rm --init \
  --env-file /Users/fuziqiang/.config/stock-strategy-api.env \
  -e STOCK_STRATEGY_DATA_DIR=/app/data \
  -e STOCK_STRATEGY_DATABASE_PATH=/app/data/strategy.sqlite3 \
  -v /Users/fuziqiang/stock-strategy-api/data:/app/data \
  stock-strategy-api:20260817-incremental \
  stock-strategy sync-data --as-of YYYY-MM-DD
```

成功验收至少同时满足：LaunchAgent `last exit code = 0`、日志 `status=sent`、`strategy_actions.status=success`，且 `result_json.errcode=0`。同一交易日再次触发应返回 `duplicate_suppressed`。

## OHLC 增量规则

- 首次无本地 parquet 时，按策略需要建立历史基线。
- 已有数据时读取最后交易日；若已覆盖目标日，不请求行情接口。
- 若未覆盖，则从“最后日期 + 1 天”抓到目标日并原子合并。
- 漏跑多日时补齐缺口区间，不只抓自然日当天。
- `ohlcv_updated`、`ohlcv_up_to_date`、`ohlcv_rows_new` 用于区分更新、无需更新和新增行数。

## 后续开发：接收群内 @ 消息

新增一个独立常驻进程，不要把接收循环塞进每日推送脚本：

1. 复用智能机器人 WebSocket 鉴权，但保持长连接并实现心跳、断线重连和退避。
2. 按文档识别消息事件，提取 `chatid`、发送者、消息 ID 和文本内容。
3. 先按消息 ID 做持久化幂等，再执行业务处理和回复。
4. 日报主动推送继续使用 `strategy_actions` 幂等键；接收消息使用独立表，避免两类幂等语义混用。
5. 以真实群 `@机器人` 的入站事件、回复成功返回码和重连恢复作为端到端验收证据。
