# 机器清单

## 跳板机

```bash
ssh -p 6041 wudun@aiwatt.net
```

通过该跳板机可访问 `192.168.110.*` 网段的内网机器。

> 使用约定：连内网机器前先直接 `ping` 目标地址，能 ping 通就直连，ping 不通再走跳板机。

## 内网开发机

本仓库在两台开发机上都有 checkout，"本机/隔壁机器"随所在机器而变，这里一律用 hostname 标注。

| hostname  | 地址                   | 备注                    |
| --------- | ---------------------- | ----------------------- |
| frank-dev | frank@192.168.110.239  | frank 的主开发机；常驻 pptp vpn10 |
| dhfan-dev | aiwatt@192.168.1.190   | 用 1.190 这个稳定地址（另有 192.168.110.200；旧记录 192.168.110.198 不可达）；常驻 pptp vpn15 |

> 现场设备、跳板、tailnet 登录方式不入本仓库（公开仓库），见私有台账 `~/.secrets/devices/` 与 skill `device-ledger`。

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
| oci-base | ubuntu@159.54.175.64    | 承载官网（www.codewatt.cn、codewatt.cn，2026-07-16 从 oci-arm 迁入）、base.codewatt.cn（基站储能模拟服务）、files.codewatt.cn（文件服务）、ft.codewatt.cn（飞书 token broker） |
| oci-arm  | ubuntu@170.9.53.38      | 承载 paseo relay 服务（paseo.codewatt.cn、relay.codewatt.cn）、期权引擎服务（opt.codewatt.cn）、PostgreSQL；**\*.codewatt.cn 证书签发机** |
| oci-amd  | ubuntu@163.192.16.235   | 承载 x.codewatt.cn、frps.codewatt.cn、ems.codewatt.cn、ctrl.codewatt.cn |

> IP 通过域名解析得到。

## 域名

| 域名             | 指向       | 备注                            |
| ---------------- | ---------- | ------------------------------- |
| eco-watt.net     | AWS 生产机 | smart-energy 云服务生产域名     |
| base.codewatt.cn | oci-base   | 基站储能模拟服务                |
| files.codewatt.cn | oci-base  | 文件服务                        |
| codewatt.cn      | oci-base   | 官网                            |
| www.codewatt.cn  | oci-base   | 官网                            |
| ft.codewatt.cn   | oci-base   | 飞书 token broker               |
| paseo.codewatt.cn | oci-arm   | paseo relay 服务                |
| relay.codewatt.cn | oci-arm   | paseo relay 服务                |
| opt.codewatt.cn  | oci-arm    | 期权引擎服务                    |
| x.codewatt.cn    | oci-amd    |                                 |
| frps.codewatt.cn | oci-amd    |                                 |
| ems.codewatt.cn  | oci-amd    |                                 |
| ctrl.codewatt.cn | oci-amd    |                                 |

## TLS 证书（\*.codewatt.cn）

- 三台 OCI 共用一张 Let's Encrypt 通配证书（`codewatt.cn` + `*.codewatt.cn`），各机 nginx 都读 `/etc/ssl/codewatt.cn/{fullchain,privkey}.pem`。
- **只在 oci-arm 签发**：ubuntu 用户的 acme.sh（`~/.acme.sh`，阿里云 DNS 验证，crontab 每天 4 次 `acme.sh --cron`），到期前 30 天自动续期。
- 续期后 acme.sh 的 reloadcmd 调用 arm 上的 `/home/ubuntu/sync-codewatt-cert.sh`：reload arm 本机 nginx，并把证书推到 oci-base、oci-amd 后 reload nginx、校验对外证书。脚本源码在仓库 `ops/codewatt-cert/sync-codewatt-cert.sh`，改动后需重新拷到 arm。运行日志：arm 上 `journalctl -t sync-codewatt-cert`。
- **例外**：oci-amd 上 frps（`/opt/frps/frps.toml` 的 `transport.tls.*`）也直接读这张证书，且只在启动时加载，续期后需 `sudo systemctl restart frps` 才生效。
- 2026-10-07 事故：同步脚本被误删，9 月续期没推到 base/amd，导致证书过期；已重建脚本并入库。
