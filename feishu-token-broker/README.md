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
| `store.py` | `state/` 下 JSON 的文件锁与原子写 |
| `feishu_client.py` | 飞书 v2 `/oauth/token` 与 v1 `/user_info` |
| `config.py` | 常量与 env 文件解析 |

飞书 refresh_token **一次性**：每次刷新都会作废旧的、下发新的。因此刷新在排他锁内进行，
新 token 原子落盘后才返回；并发请求只会触发一次刷新。

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

## 授权（首次或 refresh_token 失效后）

前置：飞书开放平台 → 安全设置 → 重定向 URL 已登记 `https://ft.codewatt.cn/oauth/callback`。

```bash
sudo -u feishu /opt/feishu/app/.venv/bin/python -m feishu_token_broker authorize
```

10 分钟内用要授权的飞书账号打开输出的链接并同意，页面显示 `Broker authorized as <姓名>` 即完成。
再次执行 authorize 会让上一条链接失效。

## 排障

```bash
systemctl status feishu-token-broker
journalctl -u feishu-token-broker -n 100 --no-pager   # JSON 日志，不含任何 token
```

## 开发

```bash
uv sync && uv run pytest -q
```
