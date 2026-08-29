# Codex 切换第三方 API Key

把 codex 从原生登录（ChatGPT 账号）切到第三方 API 网关，或在不同网关之间切换。

## 核心：三件事必须一起做

1. **换凭据** — `~/.codex/auth.json`
2. **改历史 thread 的 provider** — `~/.codex/state_*.sqlite`
3. **重启 daemon 并杀掉常驻 app-server**

漏掉任何一件都会出「看着好了其实是坏的」：新建会话能跑通，历史会话全部 401。**只跑一次 `codex exec` 验证会误判**，因为它走的是新建会话。

## 前置：确认本机参数

这几项因机器而异，操作前先查，别照抄别人的：

| 参数 | 查法 | 已知取值 |
| --- | --- | --- |
| 服务单元名 | `systemctl list-units --type=service --all \| grep -i paseo` | oci-arm 是 `paseo-daemon.service`（另有 `paseo-relay.service`）；内网开发机是 `paseo.service` |
| codex 安装路径 | `which codex \|\| ls ~/.npm-global/bin/codex ~/.nvm/versions/node/*/bin/codex` | oci-arm 在 `~/.npm-global/bin`；内网开发机在 nvm 目录下 |
| state 文件编号 | `ls ~/.codex/state_*.sqlite` | 当前是 `state_5.sqlite`，**编号会随 codex 升级往上跳** |
| web UI 端口 | `ss -lntp \| grep -i node` | oci-arm 是 `6768`；内网开发机 daemon 监听 `6767` |

> codex 常常不在非交互 shell 的 PATH 里（nvm / npm-global 的 bin 目录只在交互式 shell 生效）。通过 ssh 跑命令时用绝对路径，或先 `export PATH=$HOME/.npm-global/bin:$PATH`。

## 操作步骤

### 0. 备份（别省）

```bash
cd ~/.codex && D=$(date +%Y%m%d)
cp -a auth.json       auth.json.bak.$D
cp -a config.toml     config.toml.bak.$D
cp -a state_5.sqlite  state_5.sqlite.bak.$D
```

### 1. 停服务，杀掉常驻 app-server

```bash
sudo systemctl stop paseo-daemon.service   # 单元名见上表，两台机器不一样
pkill -f "codex[ ]app-server"
pgrep -cf "codex[ ]app-server"             # 期望 0
```

`codex[ ]app-server` 的方括号不是笔误 —— 直接写 `pkill -f "codex app-server"` 会匹配到你自己这条命令，**把 ssh 会话一起杀掉**。方括号让 pattern 匹配「codex 空格 app-server」，但 pkill 自身命令行里是带方括号的字面量，不自匹配。

app-server 启动时就把凭据读进内存常驻，不杀掉就还是旧凭据。

### 2. 写入 API key

官方命令，从 stdin 读，不留 shell history：

```bash
printf '%s' '你的KEY' | codex login --with-api-key
codex login status        # 期望：Logged in using an API key - sk-***
```

写出的 `auth.json` 形如 `{"auth_mode":"apikey","OPENAI_API_KEY":"..."}`。

> **别手写或直接 copy 别的机器的 auth.json**。手工拼的文件容易漏 `auth_mode` 字段，虽然多数情况仍能跑，但与官方格式不一致，排查时会浪费时间。用官方命令写一遍最省事。

### 3. 改 `~/.codex/config.toml`

顶部指到第三方网关，下面的 `[projects."..."]` 段**原样保留**：

```toml
model = "gpt-5.6-sol"
model_provider = "gateway"
model_reasoning_effort = "xhigh"

[model_providers.gateway]
# 具体字段见下方「Provider 配置变体」

[projects."/home/ubuntu"]
trust_level = "trusted"
# ... 其余 trusted 目录
```

两个坑：

- **provider id 不能叫 `openai`**。codex 会拒绝启动：`reserved built-in provider IDs: openai. Built-in providers cannot be overridden`。叫 `cm`、`gateway` 都行。
- **`OPENAI_BASE_URL` 环境变量没用**，codex 不认，必须写进 config.toml。

`[projects]` 是**目录信任清单**，记的是绝对路径，天生机器专属。跨机器 copy config.toml 会把这段一起换掉，导致本机目录丢失 trusted 状态、每次跑都弹确认。跨机迁移时只迁 provider 段，projects 段保留本机的。

### 4. 重钉历史会话的 provider（最容易漏）

codex 把**每个 thread 的 provider 钉死在** `state_*.sqlite` 的 `threads` 表里。不改这步，切换前建的所有会话在 paseo 里全部 401。**必须在 daemon 停着的时候改：**

```bash
python3 - <<'EOF'
import sqlite3
src = "/home/ubuntu/.codex/state_5.sqlite"      # 编号先确认
target = "gateway"                               # 目标 provider id
con = sqlite3.connect(src)
b = sqlite3.connect(src + ".bak.manual"); con.backup(b); b.close()
print("before:", list(con.execute("select model_provider,count(*) from threads group by model_provider")))
n = con.execute("update threads set model_provider=? where model_provider!=?", (target, target)).rowcount
con.commit()
print("repinned:", n)
print("after :", list(con.execute("select model_provider,count(*) from threads group by model_provider")))
EOF
```

用 `where model_provider != 目标` 而不是等值匹配 —— 一次把所有杂项来源（`openai`、上一个网关的 id）全收拢，不用关心切换前是什么状态。

### 5. 起服务，分三层验证

```bash
sudo systemctl start paseo-daemon.service
curl -s -o /dev/null -w '%{http_code}\n' --noproxy '*' http://127.0.0.1:6768/   # 期望 200
```

**第一层 · 登录模式**

```bash
codex login status        # 期望：Logged in using an API key - sk-***
```

**第二层 · 真发一次请求**（关键，只看 status 会误判）

```bash
KEY=$(python3 -c 'import json;print(json.load(open("/home/ubuntu/.codex/auth.json"))["OPENAI_API_KEY"])')
curl -s -w '\nhttp=%{http_code}\n' --noproxy '*' -X POST \
  https://api.pulsarwave.net/v1/responses \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -d '{"model":"gpt-5.6-sol","input":"say ok"}'
```

**第三层 · 端到端**

```bash
cd /tmp && codex exec --skip-git-repo-check "reply with exactly: PONG"
```

再补一条收尾检查，确认第 4 步生效：

```bash
python3 -c 'import sqlite3;print(list(sqlite3.connect("/home/ubuntu/.codex/state_5.sqlite").execute("select model_provider,count(*) from threads group by model_provider")))'
# 期望只有一个 provider
```

## Provider 配置变体

两套都在 oci-arm 上验证过，字段结构不同，别混用：

| | `cm`（CodexManager） | `gateway`（API Gateway） |
| --- | --- | --- |
| `base_url` | `https://llm-proxy-k3s6jf12sd78.zero-qa.com/v1` | `https://api.pulsarwave.net` |
| 是否带 `/v1` | **带** | **不带**，codex 自己拼 |
| 传输字段 | `wire_api = "responses"`<br>`supports_websockets = true` | 无 |
| 认证字段 | `requires_openai_auth = true`（codex ≥ 0.150 必须） | `requires_openai_auth = true` |

**cm 在不同 codex 版本上认证方式不同**（2026-08-29 实测）：0.146 会自动读 `auth.json`，不加 `requires_openai_auth` 也能跑；**0.150 起不再回落 `auth.json`**，不加就报 401 `API_KEY_REQUIRED`（codex 压根不送 key）。另一种解法是 `env_key = "OPENAI_API_KEY"` 配合 shell export 同名环境变量，但那样得改 `~/.zshrc`、非交互 shell 还拿不到，**优先用 `requires_openai_auth`**。

```toml
# cm 变体
[model_providers.cm]
name = "CodexManager"
base_url = "https://llm-proxy-k3s6jf12sd78.zero-qa.com/v1"
wire_api = "responses"
supports_websockets = true
requires_openai_auth = true      # codex >= 0.150 必须

# gateway 变体
[model_providers.gateway]
name = "API Gateway"
base_url = "https://api.pulsarwave.net"
requires_openai_auth = true
```

## 返回码怎么读

| 返回码 | 含义 |
| --- | --- |
| **200** | 好了 |
| **401** `Incorrect API key` | key 或 provider 配错；**或者第 4 步漏了** —— 去看请求 url 是不是打到了 `api.openai.com` |
| **429** `USAGE_LIMIT_EXCEEDED` / `WEEKLY_LIMIT_EXCEEDED` | key 有效但额度耗尽，跟配置无关 |

## 切回原生登录

完全对称的操作：

1. `codex login`（或还原 `auth.json.chatgpt.bak.*`）
2. config.toml 换成不带 `model_provider` 的那份
3. 第 4 步反向：`update threads set model_provider='openai'`
4. 重启 daemon

## 附：沙箱失效（Ubuntu 24.04）

codex 用 `bubblewrap` 做命令执行沙箱（限制可读写范围）。Ubuntu 23.10 起默认用 AppArmor 禁掉非特权 user namespace，导致 bwrap 起不来：

```
$ bwrap --ro-bind / / --unshare-user /bin/echo OK
bwrap: setting up uid map: Permission denied
$ sysctl kernel.apparmor_restrict_unprivileged_userns
kernel.apparmor_restrict_unprivileged_userns = 1
```

此时 codex 能正常干活，但**执行命令时没有隔离保护**。装不装 `bubblewrap` 包都一样，只是告警文案不同。要恢复沙箱需给 bwrap 单独写 AppArmor profile，或 `sysctl kernel.apparmor_restrict_unprivileged_userns=0`（等于对整个系统放开，降低安全基线）。
