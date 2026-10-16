---
name: device-ledger
description: 项目现场设备登录台账的查询与归档。当用户报告某项目设备的登录方式（IP、账号、密码、VPN、Tailscale/tailnet、frp、跳板）、要求登录/排查某项目设备、或编译部署前需要确认设备 CPU 架构与已部署服务（ems、emu、ppc、orchestrator 等）时使用。
---

# 设备登录台账

**一实一虚**：数据（“实”）在私有目录，本 skill 和全局规则（“虚”）只写去哪找、怎么记，绝不写入任何地址、账号或口令。本 skill 所在仓库是公开的。

| 内容 | 位置 | 说明 |
| --- | --- | --- |
| 台账 | `~/.secrets/devices/<项目>.md` | 地址、账号、途径、凭据位置、架构、服务；目录 700，本地 git，**禁止配置 remote** |
| 索引 | `~/.secrets/devices/README.md` | 项目列表与通用凭据约定 |
| 口令 | `~/.secrets/<项目>.env`（600） | 只能 `source` 使用，禁止输出值；台账里只写 `文件:变量名` |
| tailnet 全量电站清单 | `~/.secrets/devices/tailnet/tailnet-stations.csv` | 只知道 IP 或电站名时用它定位 tailnet |

## 查询：要登录或部署某台设备

1. `grep -ril '<项目名/电站名/IP/SN>' ~/.secrets/devices/`，读命中的项目文件；没命中就到 `tailnet/tailnet-stations.csv` 查 tailnet。
2. 按「登录」表的出发机器和途径连接：
   - tailscale：先在出发机器上运行 `tailscale status --json`，确认当前 tailnet；不对就按 `tailscale-switch` skill 切换（切换前先问用户）。
   - 口令：`set -a; . ~/.secrets/<文件>.env; set +a`，再 `sshpass -p "$VAR" ssh ...`，命令输出里不得出现口令。
3. 编译部署前，从「部署」表取架构、init 和服务路径；标“待确认”的，先上设备用 `uname -m`、`ls -l /sbin/init`、`systemctl list-units --type=service` 实测。
4. 实测结果和台账不一致时，以实测为准，回写台账并提交（见下文）。

## 归档：用户报来设备信息

用户提到某项目设备怎么登录、密码是什么、在哪个 tailnet、部署了什么，或者排查中实测出新事实时，**当场归档，不用等用户要求**：

1. 确定项目文件 `~/.secrets/devices/<项目slug>.md`：已有就更新对应行，没有就按下面的模板新建，并在 `README.md` 的项目索引里加一行。
2. 有口令就写入 `~/.secrets/<项目slug>.env`（`umask 077`，变量名用 `<设备>_SSH_PASSWORD` 这种全大写形式），台账里只写 `文件:变量名`。飞凌设备沿用 `forlinx-default.env:FORLINX_PASSWORD`，不要另存一份。
3. 缺的字段写“待确认 / 待补”，推断出来的值标“**推断**”并写明依据，**不得猜测后当成事实写入**。
4. 「核查」列写 `YYYY-MM-DD 实测` 或 `YYYY-MM-DD memory/用户告知`。
5. 提交：`git -C ~/.secrets/devices add <文件> && git -C ~/.secrets/devices commit -m "<项目>: <变更>"`。
6. 回复用户时说明记到了哪个文件、还有哪些字段待补。

## 项目文件模板

```markdown
# <项目中文名>

- 仓库：<相关仓库/目录>

## 登录

| 设备 | 主机名/SN | 地址 | 账号 | 途径 | 出发机器 | 凭据 | 核查 |
| --- | --- | --- | --- | --- | --- | --- | --- |

## 部署

| 设备 | 架构 | OS / init | 服务（安装路径） |
| --- | --- | --- | --- |

## 登录命令

## 备注
```

字段写法：

- **途径**：`tailscale(<tailnet>)` | `vpn(<名称>/<pptp|openvpn|wg>)` | `frp(<frps主机>:<端口>)` | `反向隧道(<主机>:<端口>)` | `跳板(<台账里的设备名>)` | `直连`。多段链路按顺序分行写在「登录命令」里。
- **出发机器**：`本机`（frank-dev）、`隔壁机器`（aiwatt@192.168.1.190），或 `oci-arm` 等台账里登记过的跳板；tailscale 途径要注明该机器的 profile ID。
- **架构**：用 `uname -m` 的原值（`armv7l` / `aarch64` / `x86_64`），加粗；OS / init 要写明是 systemd 还是 SysV/BusyBox，因为这决定服务怎么装、怎么启停。
- **服务**：写 systemd 单元名或 init 脚本名，加上安装路径和关键端口，例如 `ems_II.service（/opt/ems_app，:8080）`。
