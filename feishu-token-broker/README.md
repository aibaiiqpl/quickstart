# feishu-token-broker

服务端托管飞书用户的 refresh_token，向持有 Broker API key 的客户端（`access-feishu` skill 等）
发放短期 `user_access_token`。客户端接入见 [docs/client-setup.md](docs/client-setup.md)。

- 生产：frank-eu（`frank@91.90.83.210`）`/opt/feishu`，对外 `https://ft.codewatt.cn`（Caddy 反代 → `127.0.0.1:8789`）
- 2026-11-08 重建：原部署在 oci-base 上，机器失联后源码与配置一并丢失，故重建并纳入本仓库

## 结构

| 模块 | 职责 |
|---|---|
| `server.py` | HTTP 路由、Bearer 鉴权、响应格式化 |
| `service.py` | 按需刷新 token、whoami、OAuth 授权流程 |
| `keepalive.py` | 后台保活：启动即检查、之后每小时触发一次按需刷新 |
| `store.py` | `state/` 下 JSON 的文件锁与原子写 |
| `feishu_client.py` | 飞书 v2 `/oauth/token` 与 v1 `/user_info` |
| `config.py` | 常量与 env 文件解析 |

飞书 refresh_token **一次性**：每次刷新都会作废旧的、下发新的。因此刷新在排他锁内进行，
新 token 原子落盘后才返回；并发请求只会触发一次刷新。

token 有效期（以飞书返回为准）：access_token 约 2 小时，refresh_token **7 天**。
保活线程保证无人调用时 refresh_token 也持续轮换；但飞书规定**用户授权满 365 天必须重新授权**
（本次授权 2026-11-09，届时 `/v1/user_access_token` 返回 `reauthorization_required`）。

## 服务器布局

```
/opt/feishu/
  app/            代码 + .venv（root，只读）
  feishu.env      配置（root:feishu 0640），模板见 deploy/feishu.env.example
  state/          token.json、pending_oauth.json（feishu 0700/0600）
  docs/client-setup.md
```

systemd：`feishu-token-broker.service`，以系统用户 `feishu` 运行，仅 `state/` 可写。

## 部署 / 升级

```bash
# 本机
rsync -a --delete --exclude .venv --exclude __pycache__ --exclude .pytest_cache \
  feishu-token-broker/ frank@91.90.83.210:/tmp/feishu-token-broker/
# 服务器
sudo /tmp/feishu-token-broker/deploy/install.sh && rm -rf /tmp/feishu-token-broker
```

首次部署前置：`/usr/local/bin/uv`；Caddyfile 加入 `deploy/Caddyfile.snippet` 后 `sudo systemctl reload caddy`。
首次执行 `install.sh` 会放置 `feishu.env` 模板并退出，填好后再执行一次。

## 授权（首次、改 scope 或 refresh_token 失效后）

前置（飞书开放平台，应用 `cli_aac9961bb9381cb1`）：

- 安全设置 → 重定向 URL 已登记 `https://ft.codewatt.cn/oauth/callback`
- 权限管理已开通 `offline_access` 及 `FEISHU_OAUTH_SCOPE` 中的全部权限，且**已发布版本**；
  scope 里有任何一项未开通或未发布，授权页报 20027 并列出缺失项

**scope 必须逐项列出**：Broker 走 v2 授权，token 只含授权链接里申请过的权限。
旧 Broker（v1 授权）默认带应用全部权限，这些授权不会被 v2 继承——2026-11-09 留空 scope
授权后只拿到 `auth:user.id:read offline_access`。当前 `FEISHU_OAUTH_SCOPE`（应用已开通的全部用户身份权限；
表格接口由 `drive:drive` 覆盖）：

```
bitable:app docx:document docx:document:readonly docx:document:write_only docx:document:create
docx:document.block:convert drive:drive drive:drive.metadata:readonly drive:export:readonly
drive:file:download drive:file:upload wiki:node:read offline_access
```

后台新开通权限后：发布版本 → 追加到 `FEISHU_OAUTH_SCOPE` → 重新执行 authorize。

```bash
sudo -u feishu /opt/feishu/app/.venv/bin/python -m feishu_token_broker authorize
```

10 分钟内用要授权的飞书账号打开输出的链接并同意，页面显示 `Broker authorized as <姓名>` 即完成，
再用 `/v1/whoami` 核对 `scope`。再次执行 authorize 会让上一条链接失效。
scope 只在生成链接时读取，改 `feishu.env` 中的 scope 无需重启服务。

## 排障

```bash
systemctl status feishu-token-broker
journalctl -u feishu-token-broker -n 100 --no-pager   # JSON 日志，不含任何 token
```

## 开发

```bash
uv sync && uv run pytest -q
```
