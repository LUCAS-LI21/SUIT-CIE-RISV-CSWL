# device/rk2206 · 环控固件（RK2206 / OpenHarmony 3.0 轻量系统）

宠物喂食与环境控制板的固件，跑在软通「通晓」RK2206 开发板上：
OpenHarmony 3.0 轻量系统，内核 LiteOS-M，通过 MQTT 接收 `ctrl` / `ctrl3` 指令驱动喂食舵机与环境外设。

## 来源

来自 [Luo1123-tech/pet](https://github.com/Luo1123-tech/pet) 的 `txsmartropenharmony/`，
本目录只收录**项目相关的那两层**，内核与框架等 OpenHarmony 上游代码不在其列。

| 原路径 | 本仓库路径 |
|---|---|
| `txsmartropenharmony/device/rockchip/rk2206/` | `device/rk2206/bsp/` |
| `txsmartropenharmony/vendor/isoftstone/rk2206/` | `device/rk2206/product/` |

## 目录

```
bsp/                      板级支持包与 IoT HAL 适配（device/rockchip/rk2206）
├── sdk_liteos/           main.c、启动汇编、链接脚本、target_config.h、loader/*.bin（烧录用）
├── adapter/hals/         hal_iot_*.c —— 把 IoTGpio*/IoTI2c*/IoTUart* 转到芯片层 Lz* 接口
└── hardware/             芯片寄存器层（预编译库，无 .c 源码）

product/                  产品定义与外设驱动（vendor/isoftstone/rk2206）
├── config.json           产品定义（子系统 / 组件裁剪）
├── hdf_drivers/          GPIO / I2C / PWM / ADC / SPI 五个 HdfDriverEntry
├── hdf_config/           对应 .hcs 配置
├── hals/                 IoT HAL 上层接线
└── samples/              39 个样例（A 内核 / B 基础外设 / C 传感器 / D-E 综合场景）
```

样例按字母分组，`e1_iot_smart_home` 是「环控 + 云端联动」的综合例程，
`d1_smart_home` / `d2_smart_security` 是本地版。每个样例用 `APP_FEATURE_INIT` 注册自启动。

## 构建

需要**完整的 OpenHarmony 3.0 源码树**（内核 `kernel/liteos_m`、`base/`、`foundation/`、
`third_party/`、`build/lite` 等）。步骤：

1. 拉取 OpenHarmony 3.0 LTS 源码
2. 把本目录两个子目录放回上表的原路径
3. `hb set` 选择 `isoftstone-rk2206`，`hb build -f`
4. 用 Rockchip 烧录工具写 `out/rk2206/` 下的镜像（`bsp/sdk_liteos/loader/*.bin` 为 loader）

单独拿本目录是编不出来的——这是刻意的：把 30k+ 个上游文件复制进本仓库没有意义。

## 未收录

- `out/`：构建产物（20.8 MB）
- `docs/datasheet/`：ST7789V、MPU6050、SHT30 等**第三方芯片手册 PDF**，版权归各厂商，不随源码分发
- `kernel/`、`base/`、`foundation/`、`third_party/`：OpenHarmony 上游代码

## 许可证

Apache License 2.0，见 [LICENSE-Apache-2.0](LICENSE-Apache-2.0)。
注意：本仓库根目录是 MIT，**本目录及其子目录遵循 Apache-2.0**，两者不冲突但需分别遵守。
