---
name: codex-switch
description: 在指定机器上切换 codex 的账号/provider（cm、gateway、原生 ChatGPT 登录）。当用户说「把某台机器的 codex 切成 X」「换成 cm 那套 / gateway 那套 / 原生登录」「codex 换 apikey」时使用。支持本机、隔壁机器、oci-arm、mac 四台。
---

# codex 切换账号

在目标机器上把 codex CLI 切到指定账号。**完整流程见 `docs/codex-apikey-switch.md`**，本 skill 是它的参数化执行版，补充了各机器的实测参数和 key 来源。

## 参数

**机器**：`本机` | `隔壁机器` | `oci-arm` | `mac`
**账号**：`cm` | `gateway` | `原生`（ChatGPT OAuth）

两者缺一就问用户，不要猜。

## 机器矩阵（实测，2026-08-29）

| 机器 | 连接 | 平台 | codex | paseo 服务 |
| --- | --- | --- | --- | --- |
| 本机 | 本地 `frank@frank-dev` | Linux | `~/.nvm/versions/node/v26.2.0/bin/codex` 0.150.1 | `paseo.service` |
| 隔壁机器 | `ssh aiwatt@192.168.1.190` | Ubuntu 24.04 | 同上路径（另有 `/usr/local/bin/codex` 软链） | `paseo.service` |
| oci-arm | `ssh ubuntu@170.9.53.38` | Ubuntu arm64 | `~/.npm-global/bin/codex` 0.146.1 | `paseo-daemon.service` + `paseo-relay.service` |
| mac | `SSHPASS=dhfan sshpass -e ssh frankfan@192.168.110.25` | macOS 13.5.1 arm64 | `~/.nvm/versions/node/v24.15.0/bin/codex` 0.147.0 | **无 systemd**，是 `/Applications/Paseo.app` |

四台的 state 文件都是 `~/.codex/state_5.sqlite`（**编号会随 codex 升级跳，先 `ls ~/.codex/state_*.sqlite` 确认**）。

**mac 是笔记本，经常离线**（`No route to host`），先测连通性再动手。

## 账号定义与 key 来源

**绝不把 key 明文写进本文件或任何提交物**。下面只记指纹（sha256 前 12 位）和存放位置，执行时现取现验。

| 账号 | provider id | base_url | key 指纹 | 长度 |
| --- | --- | --- | --- | --- |
| cm（本机专用） | `cm` | `https://llm-proxy-k3s6jf12sd78.zero-qa.com/v1` | `c3ac3c6d511e` | 67 |
| cm（其余机器） | `cm` | 同上 | `8034de371f9c` | 12 |
| gateway | `gateway` | `https://api.pulsarwave.net` | `31631675602d` | 164 |

**cm 有两把不同的 key，按机器分配**（用户已确认：每台机器用自己历史上那把）：

| 目标机器 | 切 cm 用哪把 | 从哪取 |
| --- | --- | --- |
| 本机 | `c3ac3c6d511e`（67） | 本机当前 `~/.codex/auth.json`（**仅此一处，切走前必须备份**） |
| 隔壁机器 | `8034de371f9c`（12） | oci-arm `~/.codex/auth.json.apikey.bak.20260811` |
| oci-arm | `8034de371f9c`（12） | oci-arm `~/.codex/auth.json.apikey.bak.20260811` |
| mac | 无历史记录 | 停下来问用户 |

切 gateway 一律用 `31631675602d`，可从隔壁机器当前 `auth.json` 或 oci-arm `auth.json.gateway.bak.20260828` 取。

> **本机和隔壁机器没有任何 auth.json 备份**。本机那把 67 字符 cm key 只存在于当前 auth.json 一处，切走而不备份就永久丢失。第 0 步的备份对这两台是硬要求。

取 key 后**先验证再写入**：

```bash
curl -s -o /dev/null -w '%{http_code}\n' --noproxy '*' -m 20 \
  -H "Authorization: Bearer $K" <base_url>/models     # 期望 200
```

指纹核对（不打印明文）：

```bash
python3 -c "
import json,hashlib,sys
k=json.load(open(sys.argv[1]))['OPENAI_API_KEY']
print('len=%d %s'%(len(k),hashlib.sha256(k.encode()).hexdigest()[:12]))" <auth.json 路径>
```

## 原生登录（`原生`）

codex 的 OAuth 登录**需要浏览器交互，远程机器做不了**。唯一可行路径是还原该机的 OAuth 备份：

| 机器 | 可用备份 |
| --- | --- |
| oci-arm | `auth.json.chatgpt.bak.20260806` / `.20260811` / `.20260821` |
| mac | `auth.json.chatgpt.bak.20260827` / `.20260821` |
| 本机、隔壁机器 | **无**，只能让用户在该机本地交互跑 `codex login` |

还原后 config.toml 要去掉 `model_provider` 和 `[model_providers.*]` 段（回落内置 openai），threads 反向重钉成 `openai`。

OAuth 的 access_token 短期有效，codex 会自动用 refresh_token 续签；若 refresh_token 已被撤销则还原失败，只能交互重登。

## 执行流程

### 0. 备份（硬要求，别省）

```bash
cd ~/.codex && D=$(date +%Y%m%d)
cp -a auth.json      auth.json.<当前账号>.bak.$D
cp -a config.toml    config.toml.<当前账号>.bak.$D
cp -a state_5.sqlite state_5.sqlite.<当前账号>.bak.$D
```

state 有活跃 WAL 时 `cp` 抓不全，用 sqlite 的 backup API 做一致性备份（见第 4 步脚本内）。

### 1. 停服务、杀 app-server

```bash
# Linux
sudo systemctl stop paseo-daemon.service      # oci-arm；本机/隔壁机器是 paseo.service
pkill -f "codex[ ]app-server"
pgrep -cf "codex[ ]app-server"                # 期望 0

# mac：没有 systemd，Paseo.app 是 GUI 常驻
pkill -f "codex[ ]app-server"                 # Paseo Daemon 会按需重新拉起
```

`codex[ ]app-server` 的方括号不是笔误——直接写 `codex app-server` 会匹配到你自己这条命令，**把 ssh 会话一起杀掉**。

app-server 把凭据读进内存常驻，不杀就还是旧凭据。**它在 OAuth 模式下会定期续签并回写 auth.json，可能覆盖你刚写入的 apikey。**

**停服务会中断 paseo 上正在跑的会话**，动手前告知用户。

### 2. 写入 key

```bash
export PATH=<该机 codex 所在 bin>:$PATH        # 见机器矩阵；否则报 env: node: No such file or directory
python3 -c "import json,sys;sys.stdout.write(json.load(open('<源 auth.json>'))['OPENAI_API_KEY'])" \
  | codex login --with-api-key
codex login status                             # 期望：Logged in using an API key
```

跨机取 key 时用 ssh 管道串起来，两端都不落盘：

```bash
ssh <源机> 'python3 -c "..."' | ssh <目标机> 'codex login --with-api-key'
```

用管道而非 `printf '%s' "$KEY"`，避免 key 里的 `$` 等特殊字符被 shell 展开，也不进 history。

### 3. 改 config.toml

**只改 provider 相关的三处，其余原样保留**：顶层 `model_provider`，`[model_providers.*]` 段，以及本机原有的 `model_reasoning_effort`（本机/oci-arm 是 `xhigh`，mac 是 `high`，别互相污染）。

```toml
# cm
model_provider = "cm"
[model_providers.cm]
name = "CodexManager"
base_url = "https://llm-proxy-k3s6jf12sd78.zero-qa.com/v1"
wire_api = "responses"
supports_websockets = true

# gateway
model_provider = "gateway"
[model_providers.gateway]
name = "API Gateway"
base_url = "https://api.pulsarwave.net"
requires_openai_auth = true
```

注意 cm 的 base_url **带 `/v1`**、gateway **不带**，认证字段也不同，别混用。

**`[projects."..."]` 段是目录信任清单，记绝对路径，机器专属，绝不能跨机覆盖**（mac 有 26 个、本机 9 个、oci-arm 4 个）。同理 mac 上还有 plugins / mcp_servers / marketplaces / desktop / shell_environment_policy 等大段配置，**只做插入，绝不整文件覆盖**。

provider id **不能叫 `openai`**（codex 拒绝启动：`reserved built-in provider IDs`）。`OPENAI_BASE_URL` 环境变量不生效。

### 4. 重钉历史 thread（最容易漏）

codex 把每个 thread 的 provider 钉死在 `state_*.sqlite` 的 `threads` 表。不改这步，切换前建的会话在 paseo 里全部 401。**必须在服务停着时改：**

```bash
python3 - <<'EOF'
import sqlite3
src="<HOME>/.codex/state_5.sqlite"; target="cm"   # 改成目标 provider id
con=sqlite3.connect(src, timeout=30)
b=sqlite3.connect(src+".bak.manual"); con.backup(b); b.close()
print("before:", list(con.execute("select model_provider,count(*) from threads group by model_provider")))
n=con.execute("update threads set model_provider=? where model_provider!=?", (target,target)).rowcount
con.commit()
print("repinned:", n)
print("after :", list(con.execute("select model_provider,count(*) from threads group by model_provider")))
EOF
```

用 `where model_provider != 目标` 而非等值匹配，一次把所有杂项来源（`openai`、上一个 provider）全收拢。

### 5. 起服务，三层验证

```bash
sudo systemctl start paseo-daemon.service                                    # mac 跳过
curl -s -o /dev/null -w '%{http_code}\n' --noproxy '*' http://127.0.0.1:6768/  # oci-arm 6768；本机/隔壁 6767
```

三层缺一不可：

```bash
# 第一层：登录模式
codex login status

# 第二层：真发一次请求（只看 status 会误判）
K=$(python3 -c "import json;print(json.load(open('$HOME/.codex/auth.json'))['OPENAI_API_KEY'])")
curl -s -w '\nhttp=%{http_code}\n' --noproxy '*' -X POST <base_url>/responses \
  -H "Authorization: Bearer $K" -H 'Content-Type: application/json' \
  -d '{"model":"gpt-5.6-sol","input":"say ok"}'

# 第三层：端到端
cd /tmp && codex exec --skip-git-repo-check "reply with exactly: PONG"

# 收尾：确认第 4 步生效
python3 -c "import sqlite3;print(list(sqlite3.connect('$HOME/.codex/state_5.sqlite').execute('select model_provider,count(*) from threads group by model_provider')))"
```

**只跑 `codex exec` 会误判**——它走新建会话，历史 thread 全坏也照样返回 PONG。必须查 threads 分布。

返回码：**200** 好了；**401 `Incorrect API key`** = key 或 provider 配错，或第 4 步漏了；**429 `USAGE_LIMIT_EXCEEDED`** = key 有效但额度耗尽，与配置无关。

## 平台差异（macOS）

1. **`node` 不在非交互 PATH**，codex 是 node 脚本，用绝对路径仍报 `env: node: No such file or directory` → 必须 `export PATH="$HOME/.nvm/versions/node/v24.15.0/bin:$PATH"`
2. **系统 python3 无 `tomllib`**（<3.11）→ 校验 TOML 改用 `codex login status` 间接验证
3. **无 `timeout` 命令**
4. **无 systemd**，app-server 由 `/Applications/Paseo.app` 经 `Paseo Supervisor → Paseo Daemon` 拉起
5. 备份 sqlite 必须用 `con.backup()`，该机 WAL 常有数 MB

## 安全铁律

- **脱敏必须递归处理嵌套字典**。OAuth 的 auth.json 把 token 放在 `tokens` 子字典里，只做顶层 `{k: len(v)}` 会把 id_token / access_token / **refresh_token 整个明文打印出来**（已发生过一次事故）。
- 只打印长度和 sha256 前 12 位，永不打印 key/token 明文。
- 跨机传 key 走 ssh 管道，不落盘、不进 history。
- 切走前必须备份当前 auth.json，否则该账号的 key 可能永久丢失。

## 已知遗留问题

以下是历史切换中漏做第 4 步造成的，执行本 skill 时会被自动修好（`!=` 写法会收拢它们），也可单独修：

- **本机**：threads 里有 22 个钉在 `openai`（config 是 cm），会打到 api.openai.com
- **隔壁机器**：threads 里有 4 个钉在 `cm`，而 config 已是 gateway、`[model_providers.cm]` 段已删除

## 回退

每次执行都在 `~/.codex/` 留了 `*.<账号>.bak.<日期>`。回退 = 反向跑一遍本流程：还原 auth.json 与 config.toml，threads 重钉回原 provider，重启服务。
