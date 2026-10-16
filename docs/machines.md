# 机器清单

> 本文只记开发与云端基础设施，不含密码。项目现场设备、部署目标的架构/glibc/服务清单，以及所有登录密码，统一在本机设备台账 `~/.secrets/machines/<project>.json`（`$device-access` Skill）。

## 跳板机

```bash
ssh -p 6041 wudun@aiwatt.net
```

通过该跳板机可访问 `192.168.110.*` 网段的内网机器。

> 使用约定：连内网机器前先直接 `ping` 目标地址，能 ping 通就直连，ping 不通再走跳板机。

## 内网开发机

内网开发机均为 VirtualBox 虚拟机，宿主机见下方「虚拟化宿主机」。`192.168.110.*` 多为 DHCP 分配、**会漂移**，下表地址为 2026-08-03 实测值。

| 别名/命令 | 地址                   | 宿主机上的虚机名 | 备注                    |
| --------- | ---------------------- | ---------------- | ----------------------- |
| xmy-dev   | aiwatt@192.168.110.200 | `xmy-dev`        | 本机（hostname 为 dhfan-dev），网卡 enp0s3；110.200 由 DHCP 得来，同网卡另有 `192.168.1.190/24` |
| frank-dev | **frank@192.168.1.239**（固定，优先用） | `frank-test`     | 隔壁机器。2026-08-03 加挂固定地址，见下方「192.168.1.0/24 网段」；同网卡仍保留 `192.168.110.239/24`（同为 netplan 静态，非 DHCP） |

### 192.168.1.0/24 网段

与 `192.168.110.0/24` 在**同一二层**共存的另一个网段，**没有网关**（默认路由仍走 `192.168.110.1`），所以在这个网段上只配地址、不配路由和 DNS。

隔壁机器的固定地址就挂在这里，配置写在 `/etc/netplan/50-cloud-init.yaml`（Ubuntu 24.04 + systemd-networkd；该机 `cloud-init status` 为 `disabled`，不会重写 netplan，所以直接改这个文件即可）：

```yaml
network:
  version: 2
  ethernets:
    enp0s3:
      dhcp4: false
      addresses:
        - 192.168.110.239/24   # 原有地址，不能动
        - 192.168.1.239/24     # 加挂的固定地址
```

> 加挂第二地址是**纯追加**操作：改 netplan 时必须把原有地址一起列出（netplan 对同一网卡的 `addresses` 是**整键覆盖**，高编号文件会替换而不是追加，漏写原地址会直接断网）。改完先 `netplan generate` 校验、`ip addr add` 运行时验证，再 `netplan apply`。

## 内网物理机

不挂在 VirtualBox 宿主机上的内网机器，地址同样由 DHCP 分配、**会漂移**。

| 别名      | 地址                    | 备注                    |
| --------- | ----------------------- | ----------------------- |
| frank-mac | frankfan@192.168.110.26 | Frank 的 MacBook Air（hostname `FrankdeMacBook-Air.local`，Apple Silicon）。**只能密码登录**，未配公钥，密码不入库（见本机设备台账 `~/.secrets/machines/office.json`） |

关于这台机器上的 codex / paseo，有几处与 Linux 开发机不同：

- **paseo 是桌面 App**（`/Applications/Paseo.app`，进程为 Paseo Supervisor → Paseo Daemon），**没有 systemd/launchd 服务**，不能 `systemctl restart`，只能手动重启 App。
- **`codex` 不在 PATH**。App 自带的 CLI 在 `/Applications/ChatGPT.app/Contents/Resources/codex`；给 paseo 跑 app-server 的是另一份 nvm 装的 `@openai/codex`。
- 站点 VPN 走 macOS 专用的一套（`/usr/local/libexec/aiwatt-pptp/pptp-wrapper.sh` + `/usr/local/bin/pptp-codex`），由 codex 托管；`pppd` 会 reparent 到 launchd，所以杀 codex 进程不会断掉隧道。

## 虚拟化宿主机

| 别名     | 地址               | 备注                                          |
| -------- | ------------------ | --------------------------------------------- |
| vbox-host | wudun@192.168.110.3 | VirtualBox 宿主机，承载 18 台开发虚机；根分区 901G，`~/VirtualBox VMs` 常年逼近写满 |

### 内网机器 ping 不通时的排查顺序

不要直接判定「机器关机了」。宿主机磁盘写满时，VirtualBox 会把虚机自动切到 `paused` 以防数据损坏，此时虚机 CPU 冻结、网卡不响应，表现与关机完全一致（ping 不通 + ARP FAILED + SSH 连接超时）。

```bash
ssh wudun@192.168.110.3
VBoxManage list vms                                          # 虚机清单
VBoxManage showvminfo <vm> --machinereadable | grep VMState  # paused?
df -h /                                                      # 宿主机磁盘
tail -30 ~/VirtualBox\ VMs/<vm>/Logs/VBox.log | grep -i disk_full
```

确认是 paused 后，**必须先在宿主机腾出空间再恢复**，否则虚机跑起来立刻会被再次挂起：

```bash
VBoxManage controlvm <vm> resume
```

恢复后进虚机确认文件系统没被踢成只读：`mount | grep " / "`（应为 `rw`）。

> 找不到虚机新地址时，用 `known_hosts` 里的主机密钥反查：主机密钥存在虚机内部、跟着机器走，换 IP 也不变。`ssh-keygen -F <旧IP>` 取出密钥，再用 `ssh-keyscan` 扫全网段比对即可定位。

## 其他

| 别名        | 地址                  | 备注                              |
| ----------- | --------------------- | --------------------------------- |
| AWS 测试机   | ubuntu@63.184.212.50  | AWS 云上测试机                    |
| AWS 生产机   | ubuntu@18.158.183.210 | 部署 smart-energy 云服务（生产），域名 eco-watt.net |
| 阿里云测试机 | root@223.6.248.244    | 阿里云云上测试机                  |
| 阿里云私人机 | work@8.142.40.30      | 阿里云私人机器                    |

## OCI（共 3 台）

| 别名     | 地址                    | 备注                                                         |
| -------- | ----------------------- | ------------------------------------------------------------ |
| oci-base | ubuntu@159.54.175.64    | 承载 base.codewatt.cn（基站储能模拟服务）、files.codewatt.cn（文件服务） |
| oci-arm  | ubuntu@170.9.53.38      | 承载官网（www.codewatt.cn、codewatt.cn）、paseo relay 服务（paseo.codewatt.cn、relay.codewatt.cn）、期权引擎服务（opt.codewatt.cn） |
| oci-amd  | ubuntu@163.192.16.235   | 承载 x.codewatt.cn、frps.codewatt.cn |

> IP 通过域名解析得到。

## 域名

| 域名             | 指向       | 备注                            |
| ---------------- | ---------- | ------------------------------- |
| eco-watt.net     | AWS 生产机 | smart-energy 云服务生产域名     |
| file.eco-watt.net | AWS 测试机（经 ELB） | 文件服务，nginx root `/home/files`；`tmp/<类别>/` 为临时部署中转，`ota/` 为正式 OTA |
| base.codewatt.cn | oci-base   | 基站储能模拟服务                |
| files.codewatt.cn | oci-base  | 文件服务                        |
| codewatt.cn      | oci-arm    | 官网                            |
| www.codewatt.cn  | oci-arm    | 官网                            |
| paseo.codewatt.cn | oci-arm   | paseo relay 服务                |
| relay.codewatt.cn | oci-arm   | paseo relay 服务                |
| opt.codewatt.cn  | oci-arm    | 期权引擎服务                    |
| x.codewatt.cn    | oci-amd    |                                 |
| frps.codewatt.cn | oci-amd    |                                 |
