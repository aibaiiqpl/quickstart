---
name: device-ledger
description: 项目现场设备的登录与台账查询/归档。当用户报告某项目设备的登录方式（IP、账号、密码、VPN、Tailscale/tailnet、frp、跳板）、要求登录/排查某项目设备、或编译部署前需要确认设备 CPU 架构与已部署服务（ems、emu、ppc、orchestrator 等）时使用。
---

# 设备登录与台账

本 skill 所在仓库是公开的，这里只写方法，**不得写入任何地址、账号或口令**。数据分三处，同一信息只写一处：

| 内容 | 位置 |
| --- | --- |
| SSH 参数（HostName/Port/User/ProxyJump/ProxyCommand/HostKeyAlias） | `~/.ssh/config.d/machines.conf`（`~/.ssh/config` 顶部 `Include config.d/*.conf`），登录直接用 `ssh <别名>` |
| 台账：网络前提（tailnet/VPN/frp）、出发机器、凭据位置、CPU 架构、OS/init、服务、核查记录 | `~/.local/share/machines/<项目>.md`，索引 `README.md`；本地 git，**禁止配置 remote** |
| 口令 | `~/.secrets/<项目>.env`（600），只能 `source` 使用，禁止输出值 |
| tailnet 全量电站清单 | `~/.local/share/machines/tailnet/tailnet-stations.csv` |

## 查询：要登录或部署某台设备

1. `grep -ri '<项目/电站/IP/SN/别名>' ~/.local/share/machines/ ~/.ssh/config.d/machines.conf`，读命中的项目文件；都没命中就到 `tailnet/tailnet-stations.csv` 查 tailnet。
2. 满足网络前提：tailscale 设备先在出发机器上执行 `tailscale status --json`，确认当前 tailnet；不对就按 `tailscale-switch` skill 切换（切换前先问用户）。出发机器是 dhfan-dev 的，别名里已经写了 ProxyJump。
3. 登录：公钥设备直接 `ssh <别名>`；口令设备在脚本里用
   `. ~/.secrets/<文件>.env; SSHPASS="$VAR" sshpass -e ssh <别名> '<cmd>'`（`-e` 从环境变量读口令，口令不会出现在命令行参数里）。中间跳板需要口令的，别名的 ProxyCommand 已自动处理。
4. 编译部署前，从「部署」表取架构、init 和服务路径；标“待确认”的，先上设备用 `uname -m`、`ls -l /sbin/init`、`systemctl list-units --type=service` 实测。
5. 连不上时要逐段定位（出发机器的 tailnet、隧道接口、跳板），不能直接下“设备离线”的结论；实测结果和台账不一致时，以实测为准并回写。

## 归档：用户报来设备信息

用户提到某项目设备怎么登录、密码是什么、在哪个 tailnet、部署了什么，或者排查中实测出新事实时，**当场归档，不用等用户要求**：

1. **SSH 参数** → `machines.conf` 按项目分段加 `Host <项目>-<角色>`：
   - 内网地址（10/172.16/192.168）必须加 `HostKeyAlias <别名>`，因为各站内网 IP 会重复。
   - 经其他机器出发的用 `ProxyJump`；中间跳板要口令时用
     `ProxyCommand sh -c '. ~/.secrets/<f>.env; SSHPASS="$VAR" exec sshpass -e ssh -W %h:%p <跳板别名>'`。
   - 把新别名加进文件末尾公共块的 Host 模式列表里。**不得写 `Host *`**，否则会影响主配置里的其他主机。
   - 写完用 `ssh -G <别名>` 核对解析结果。
2. **口令** → `~/.secrets/<项目>.env`（`umask 077`，变量名 `<设备>_SSH_PASSWORD`）。飞凌设备沿用 `forlinx-default.env:FORLINX_PASSWORD`，不要另存一份。
3. **台账** → `~/.local/share/machines/<项目>.md`：已有就更新，没有就按下面的模板新建，并在 `README.md` 的项目索引里加一行。台账里不写地址、账号、端口（这些归 ssh config 管）。
4. 缺的字段写“待确认 / 待补”，推断出来的值标“**推断**”并写明依据，**不得把猜测当成事实写入**。「核查」列写 `YYYY-MM-DD 实测` 或 `YYYY-MM-DD memory/用户告知`。
5. 提交：`git -C ~/.local/share/machines add <文件> && git -C ~/.local/share/machines commit -m "<项目>: <变更>"`。
6. 回复用户时说明新增了哪个别名、记到了哪个文件、还有哪些字段待补。

## 项目文件模板

```markdown
# <项目中文名>

- 仓库：<相关仓库/目录>

## 登录

| 设备 | SSH 别名 | 主机名/SN | 网络前提 | 出发机器 | 凭据 | 核查 |
| --- | --- | --- | --- | --- | --- | --- |

## 部署

| 设备 | 架构 | OS / init | 服务（安装路径） |
| --- | --- | --- | --- |

## 备注
```

字段写法：

- **网络前提**：`tailscale <tailnet>` | `vpn<名称>（pptp/openvpn/wg）` | `frp <frps主机>:<端口>` | `<主机> 反向隧道 :<端口>` | `直连`。
- **出发机器**：`本机`（frank-dev）、`dhfan-dev`，或台账里登记过的跳板；tailscale 要写明该机器上的 profile ID。
- **架构**：用 `uname -m` 的原值（`armv7l` / `aarch64` / `x86_64`），加粗；OS / init 要写明是 systemd 还是 SysV/BusyBox，因为这决定服务怎么装、怎么启停。
- **服务**：写 systemd 单元名或 init 脚本名，加上安装路径和关键端口，例如 `ems_II.service（/opt/ems_app，:8080）`。
