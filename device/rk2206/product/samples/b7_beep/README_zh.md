# 通晓开发板OpenHarmonyOS基础外设开发-蜂鸣器PWM控制

## 实验内容

本示例将演示如何在通晓开发板上使用pwm驱动蜂鸣器。

![通晓开发板](/vendor/isoftstone/rk2206/docs/figures/tx_smart_r-rk2206.jpg)

## 实验设计

### 硬件设计

![蜂鸣器原理图](/vendor/isoftstone/rk2206/docs/figures/beep/蜂鸣器原理图.jpg)

![蜂鸣器与开发板连接](/vendor/isoftstone/rk2206/docs/figures/beep/蜂鸣器与开发板连接.jpg)

从硬件原理图可以知道：

- 输出高电平，三极管导通，蜂鸣器启动
- 输出低电平，三极管截止，蜂鸣器关闭

### 软件设计

#### 导入PWM头文件

```c
#include "iot_pwm.h"
```

[pwm接口文档](/device/rockchip/hardware/docs/PWM.md)

#### PWM初始化分析

蜂鸣器引脚GPIO0_C5对应EPWMDEV_PWM5_M0。

```c
/* 定义PWM设备数量 */
enum EnumPwmDev {
    EPWMDEV_PWM0_M1 = 0,    /* GPIO_PB4 */
    EPWMDEV_PWM1_M1,        /* GPIO_PB5 */
    EPWMDEV_PWM2_M1,        /* GPIO_PB6 */
    EPWMDEV_PWM0_M0,        /* GPIO_PC0 */
    EPWMDEV_PWM1_M0,        /* GPIO_PC1 */
    EPWMDEV_PWM2_M0,        /* GPIO_PC2 */
    EPWMDEV_PWM3_M0,        /* GPIO_PC3 */
    EPWMDEV_PWM4_M0,        /* GPIO_PC4 */
    EPWMDEV_PWM5_M0,        /* GPIO_PC5 */
    EPWMDEV_PWM6_M0,        /* GPIO_PC6 */
    EPWMDEV_PWM7_M0,        /* GPIO_PC7 */
    EPWMDEV_PWM7_M1,        /* GPIO_PD0 */
    EPWMDEV_MAX
};

/* 蜂鸣器对应PWM */
#define BEEP_PORT EPWMDEV_PWM5_M0
```

使用 `IoTPwmInit()` 初始化pwm。

```c
/* 初始化PWM */
ret = IoTPwmInit(BEEP_PORT);
if (ret != 0) {
    printf("IoTPwmInit failed(%d)\n", BEEP_PORT);
}
```

#### PWM输出

使用 `IoTPwmStart()` 启动pwm输出。

```c
/* 开启PWM */
ret = IoTPwmStart(BEEP_PORT, duty, 1000);
if (ret != 0) {
    printf("IoTPwmStart failed(%d)\n");
    continue;
}
```

#### PWM停止

使用 `IoTPwmStop()` 停止pwm输出。

```c
/* 关闭PWM */
ret = IoTPwmStop(BEEP_PORT);
if (ret != 0) {
    printf("IoTPwmStart failed(%d)\n");
    continue;
}
```

## 编译调试

### 修改 BUILD.gn 文件

修改 `vendor/isoftstone/rk2206/sample` 路径下 BUILD.gn 文件，指定 `beep_example` 参与编译。

```r
"./b7_beep:beep_example",
```

修改 `device/rockchip/rk2206/sdk_liteos` 路径下 Makefile 文件，添加 `-lbeep_example` 参与编译。

```r
hardware_LIBS = -lhal_iothardware -lhardware -lbeep_example
```

### 运行结果

示例代码编译烧录代码后，按下开发板的RESET按键，通过串口助手查看日志，随着duty增大，蜂鸣器的声音变大。

```r
===========================
PWM(8) Start
duty: 10
[HAL INFO] channel=1, period_ns=1000000, duty_ns=100000
[HAL INFO] channel=1, period=40000, duty=4000, polarity=0
[HAL INFO] Enable channel=1
[HAL INFO] Disable channel=1
===========================
PWM(8) Start
duty: 20
[HAL INFO] channel=1, period_ns=1000000, duty_ns=200000
[HAL INFO] channel=1, period=40000, duty=8000, polarity=0
[HAL INFO] Enable channel=1
[HAL INFO] Disable channel=1
===========================
PWM(8) Start
duty: 30
[HAL INFO] channel=1, period_ns=1000000, duty_ns=300000
[HAL INFO] channel=1, period=40000, duty=12000, polarity=0
[HAL INFO] Enable channel=1
[HAL INFO] Disable channel=1
===========================
PWM(8) Start
duty: 40
[HAL INFO] channel=1, period_ns=1000000, duty_ns=400000
[HAL INFO] channel=1, period=40000, duty=16000, polarity=0
[HAL INFO] Enable channel=1
[HAL INFO] Disable channel=1
===========================
PWM(8) Start
duty: 50
[HAL INFO] channel=1, period_ns=1000000, duty_ns=500000
[HAL INFO] channel=1, period=40000, duty=20000, polarity=0
[HAL INFO] Enable channel=1
[HAL INFO] Disable channel=1
===========================
PWM(8) Start
duty: 60
[HAL INFO] channel=1, period_ns=1000000, duty_ns=600000
[HAL INFO] channel=1, period=40000, duty=24000, polarity=0
[HAL INFO] Enable channel=1
[HAL INFO] Disable channel=1
===========================
PWM(8) Start
duty: 70
[HAL INFO] channel=1, period_ns=1000000, duty_ns=700000
[HAL INFO] channel=1, period=40000, duty=28000, polarity=0
[HAL INFO] Enable channel=1
[HAL INFO] Disable channel=1
===========================
PWM(8) Start
duty: 80
[HAL INFO] channel=1, period_ns=1000000, duty_ns=800000
[HAL INFO] channel=1, period=40000, duty=32000, polarity=0
[HAL INFO] Enable channel=1
[HAL INFO] Disable channel=1
===========================
PWM(8) Start
duty: 90
[HAL INFO] channel=1, period_ns=1000000, duty_ns=900000
[HAL INFO] channel=1, period=40000, duty=36000, polarity=0
[HAL INFO] Enable channel=1
[HAL INFO] Disable channel=1

...
```

