# 机器清单

## 跳板机

```bash
ssh -p 6041 wudun@aiwatt.net
```

通过该跳板机可访问 `192.168.110.*` 网段的内网机器。

> 使用约定：连内网机器前先直接 `ping` 目标地址，能 ping 通就直连，ping 不通再走跳板机。

## 内网开发机

| 别名/命令 | 地址                   | 备注                    |
| --------- | ---------------------- | ----------------------- |
| xmy-dev   | aiwatt@192.168.110.198 | 本机（hostname 为 dhfan-dev），网卡 enp0s3 |
| frank-dev | frank@192.168.110.239  | 隔壁机器                |

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
| base.codewatt.cn | oci-base   | 基站储能模拟服务                |
| files.codewatt.cn | oci-base  | 文件服务                        |
| codewatt.cn      | oci-arm    | 官网                            |
| www.codewatt.cn  | oci-arm    | 官网                            |
| paseo.codewatt.cn | oci-arm   | paseo relay 服务                |
| relay.codewatt.cn | oci-arm   | paseo relay 服务                |
| opt.codewatt.cn  | oci-arm    | 期权引擎服务                    |
| x.codewatt.cn    | oci-amd    |                                 |
| frps.codewatt.cn | oci-amd    |                                 |
