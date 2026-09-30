---
name: tailscale-switch
description: 列出或切换本机/隔壁机器登录过的 Tailscale tailnet（profile）。当用户说「列一下登录过哪些 tailnet」「把本机/隔壁机器的 tailscale 切到 X」「切到 qnvpn / hj.vpn / dgjvpn 那个网」时使用。
---

# Tailscale 切换 tailnet

一台机器可以同时登录多个 tailnet（profile），同一时刻只有一个生效。本 skill 负责**列出 profile** 和**切换 profile**。

注意"登录过哪些 tailnet"指的是 `tailscale switch --list`，**不是** `tailscale status`（后者只列当前 tailnet 里的节点）。

## 参数

**机器**：`本机` | `隔壁机器`，没说就问用户。
**目标 tailnet**：tailnet 名（如 `qnvpn@outlook.com`）或 profile ID。只列 profile 时不需要这个参数。

## 机器与 profile（实测，2026-09-29）

| 机器 | 执行方式 |
| --- | --- |
| 本机 frank-dev | 本地执行 |
| 隔壁机器 dhfan-dev | `ssh aiwatt@192.168.1.190`（走局域网，切 tailnet 不会断 ssh） |

两台机器都要 `sudo`，免密（`sudo -n` 可用）。不加 sudo 会报 `profiles access denied`。

**本机**

| ID | Tailnet |
| --- | --- |
| 030f | fukuken@126.com（MagicDNS `tail64b94c.ts.net`，本机 IP 100.84.200.73） |
| 828c | einfraems@outlook.com（默认，公司主网，MagicDNS `tail0effcf.ts.net`） |
| 14db | hj.vpn@outlook.com |
| 803b | qnvpn@outlook.com（MagicDNS `taildbd071.ts.net`，本机 IP 100.74.65.34） |
| 77e9 | zhao20251123@outlook.com |

**隔壁机器**

| ID | Tailnet |
| --- | --- |
| 2c51 | dgjvpn@outlook.com |
| 1017 | jp-vpn@outlook.com |
| 1910 | evia.net.au（默认） |

所有 profile 的登录账号都是 `einfraems@outlook.com`，只能按账号区分不了，所以**切换一律用 ID**。上面的表可能过时，执行前先 `--list`，以实时结果为准。

## 列出 profile

```bash
sudo -n tailscale switch --list                                        # 本机
ssh -o ConnectTimeout=8 aiwatt@192.168.1.190 'sudo -n tailscale switch --list'   # 隔壁机器
```

带 `*` 的是当前生效的 profile。

## 切换流程

### 1. 先检查会不会断掉连接

切换后这台机器的旧 Tailscale IP 会立刻失效，所有经旧 IP 进来的连接都会断。先查有没有：

```bash
IPS=$(tailscale ip | paste -sd'|' | sed 's/\./\\./g')
ss -tnp | grep -E "$IPS" || echo "(none)"
```

如果有连接（比如用户正通过 Tailscale 远程连着这台机器），先告诉用户会断，得到同意再切。

### 2. 切换

```bash
sudo -n tailscale switch <ID>        # 期望输出：Switching to account "<ID>" / Success.
```

切换是即时的，不需要重新授权，也不用重启 tailscaled。

### 3. 验证

```bash
tailscale status --json | python3 -c 'import json,sys;d=json.load(sys.stdin);t=d.get("CurrentTailnet") or {};p=d.get("Peer") or {};print("BackendState:",d["BackendState"],"| Tailnet:",t.get("Name"),"| Self:",d["Self"]["TailscaleIPs"],"| peers:",len(p),"online:",sum(1 for v in p.values() if v.get("Online")))'
tailscale ping -c 3 --timeout 5s <任一在线节点IP>   # 期望收到 pong
```

以下几点都满足才算切换成功：`BackendState` 是 `Running`，`Tailnet` 是目标网，`ping` 能收到 pong。

如果只能走 DERP 中继、建不起直连，`tailscale ping` 会打印 `direct connection not established` 并返回 exit 1。这种情况只要收到了 pong 就算通，不是切换失败。

`BackendState` 如果是 `NeedsLogin`，说明这个 profile 的登录已过期。`sudo tailscale up` 会给出一个登录网址，把网址交给用户，让用户在浏览器里授权，不要自己想办法绕过。

## 回退

用同样的命令切回原来的 ID。本机默认是 `828c`（einfraems@outlook.com），隔壁机器默认是 `1910`（evia.net.au）。
