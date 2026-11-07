# device/hi3863 · 星闪固件（WS63V100 / SLE）

基于海思 WS63V100（星闪 NearLink）套件 PRO 的 SLE 客户端 / 服务端样例，
**24 个源文件**，是 `isoh_nl63pro/app/` 下同名目录的独立副本。

## 来源

来自 [Luo1123-tech/pet](https://github.com/Luo1123-tech/pet) 的 `nearlink_ws63/`。

## 两块板怎么联动

RK2206 与 WS63 之间**没有 SLE 直连**，走 MQTT 云端中转：

```
WS63 server（GPIO11 按键）
  → SLE 通知 "key opening"
  → WS63 client（SSAP notification 回调）
  → MQTT 发布到 ctrl3
  → RK2206 订阅 ctrl / ctrl3，触发喂食舵机
```

## 目录

```
locker_sle_client/        SLE 客户端
├── sle_client_app.c      SSAP 客户端主流程
├── mqtt_module.c/.h      WiFi + MQTT 接入（SSID/口令是占位符）
├── wifi_connecter.c      扫描并按 SSID 匹配热点
├── adc_key_example.c     按键
├── aht30.c / aw2023.c    温湿度、灯带
└── BUILD.gn

locker_sle_server/        SLE 服务端（广播 + 按键触发通知）
```

## 构建

依赖外部 WS63 SDK：`isoh_nl63pro/device/soc/hisilicon/ws63v100/`，
板级配置 `ohos_config.json` 里 `board=nl63pro`、`kernel=liteos_m`。
把这两个目录放回 `isoh_nl63pro/app/` 下，再用该 SDK 的 `hb` 构建。

## 注意

`mqtt_module.h` 里的 `WIFI_SSID` / `WIFI_PSK` / `MQTT_PASSWORD` 都是
`YOUR_*` 占位符，**没有任何真实凭据**。跑之前改成自己的，且不要提交回去。

## 许可证

Apache License 2.0，见 [LICENSE-Apache-2.0](LICENSE-Apache-2.0)（源文件头为
iSoftStone Open Source Organization 的 Apache-2.0 声明）。
注意：本仓库根目录是 MIT，本目录遵循 Apache-2.0。
