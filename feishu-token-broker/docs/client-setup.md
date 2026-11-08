# Feishu Token Broker 客户端接入

Broker 地址：`https://ft.codewatt.cn`。客户端只持有 Broker API key，从不接触飞书 refresh_token 或 app secret。

## 1. 配置

`~/.secrets/feishu.env`（目录 0700，文件 0600），只允许这两项：

```env
FEISHU_BROKER_URL=https://ft.codewatt.cn
FEISHU_BROKER_API_KEY=<向管理员获取>
```

API key 只能经加密渠道或在目标机上直接写入文件的方式传递，不得出现在聊天、仓库、日志里。

## 2. 验证

```bash
set -a; . ~/.secrets/feishu.env; set +a
curl --fail -sS "$FEISHU_BROKER_URL/health"
curl --fail -sS -H "Authorization: Bearer $FEISHU_BROKER_API_KEY" "$FEISHU_BROKER_URL/v1/whoami"
```

`/health` 返回 `{"ok": true, "service": "feishu-token-broker"}`；`/v1/whoami` 返回
`name`、`open_id_present: true`、`scope` 与两个 token 的剩余秒数。

## 3. 取 token 调飞书

`GET /v1/user_access_token`（Bearer 鉴权）返回 `access_token`、`token_type`、`expires_in`，
保证至少还有 5 分钟有效。token 只留在进程内存，用完即丢，不要写文件或打印。

## 错误码

| HTTP | error | 含义 / 处理 |
|---|---|---|
| 401 | `unauthorized` | API key 缺失或错误，向管理员确认 |
| 503 | `not_authorized` | Broker 未授权或 refresh_token 过期，需管理员重新授权 |
| 502 | `reauthorization_required` | 飞书判定 refresh_token 失效（`feishu_code` 给出原因），需管理员重新授权 |
| 502 | `feishu_refresh_failed` | 飞书刷新接口临时失败，可稍后重试 |
