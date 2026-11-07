# 通晓开发板OpenHarmonyOS基础外设开发-RGB灯PWM控制

## 实验内容

本示例将演示如何在通晓开发板上使用pwm驱动RGB灯。

![通晓开发板](/vendor/isoftstone/rk2206/docs/figures/tx_smart_r-rk2206.jpg)

## 实验设计

### 硬件设计

![rgb原理图](/vendor/isoftstone/rk2206/docs/figures/rgb/rgb原理图.jpg)

![rgb与开发板连接](/vendor/isoftstone/rk2206/docs/figures/rgb/rgb与开发板连接.jpg)

从硬件原理图可以知道：

- 输出高电平，三极管导通，灯亮
- 输出低电平，三极管截止，灯灭

### 软件设计

#### 导入PWM头文件

```c
#include "iot_pwm.h"
```

[pwm接口文档](/device/rockchip/hardware/docs/PWM.md)

#### PWM初始化分析

电机引脚GPIO1_D0、GPIO0_B5、GPIO0_B4对应EPWMDEV_PWM7_M1、EPWMDEV_PWM1_M1、EPWMDEV_PWM0_M1。

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

/* RGB对应的PWM通道 */
#define LED_R_PORT EPWMDEV_PWM1_M1
#define LED_G_PORT EPWMDEV_PWM7_M1
#define LED_B_PORT EPWMDEV_PWM0_M1
```

使用 `IoTPwmInit()` 初始化pwm。

```c
/* 初始化PWM */
ret = IoTPwmInit(LED_R_PORT);
if (ret != 0) {
    printf("IoTPwmInit failed(%d)\n", LED_R_PORT);
}

ret = IoTPwmInit(LED_G_PORT);
if (ret != 0) {
    printf("IoTPwmInit failed(%d)\n", LED_G_PORT);
}

ret = IoTPwmInit(LED_B_PORT);
if (ret != 0) {
    printf("IoTPwmInit failed(%d)\n", LED_B_PORT);
}
```

#### PWM输出

使用 `IoTPwmStart()` 启动pwm输出。

```c
/* 启动PWM */
ret = IoTPwmStart(LED_R_PORT, duty, 1000);
if (ret != 0) {
    printf("IoTPwmStart failed(%d)\n", LED_R_PORT);
    continue;
}

ret = IoTPwmStart(LED_G_PORT, duty, 1000);
if (ret != 0) {
    printf("IoTPwmStart failed(%d)\n", LED_G_PORT);
    continue;
}

ret = IoTPwmStart(LED_B_PORT, duty, 1000);
if (ret != 0) {
    printf("IoTPwmStart failed(%d)\n", LED_B_PORT);
    continue;
}
```

#### 调节占空比

通过修改占空比duty，修改灯的亮度。duty由1到99，灯由暗变亮。duty到99后，由99到1，灯由亮变暗。如此反复形成呼吸灯的效果。

```c
/* 占空比由1~99 当到99时翻转 由99~1*/
if (toggle)
{
    duty--;
}
else
{
    duty++;
}

if (duty == 99)
{
    toggle = 1;
}
else if (duty == 1)
{
    toggle = 0;
}
```

## 编译调试

### 修改 BUILD.gn 文件

修改 `vendor/isoftstone/rk2206/sample` 路径下 BUILD.gn 文件，指定 `rgb_led_example` 参与编译。

```r
"./b9_rgb_led:rgb_led_example",
```

修改 `device/rockchip/rk2206/sdk_liteos` 路径下 Makefile 文件，添加 `-rgb_led_example` 参与编译。

```r
hardware_LIBS = -lhal_iothardware -lhardware -lrgb_led_example
```

### 运行结果

示例代码编译烧录代码后，按下开发板的RESET按键，通过串口助手查看日志，任务每隔5s控制不同PWM输出。

```r
===========================
PWM(1) Start
PWM(11) Start
PWM(0) Start
duty: 94
[HAL INFO] channel=1, period_ns=1000000, duty_ns=940000
[HAL INFO] channel=1, period=40000, duty=37600, polarity=0
[HAL INFO] Enable channel=1
[HAL INFO] channel=3, period_ns=1000000, duty_ns=940000
[HAL INFO] channel=3, period=40000, duty=37600, polarity=0
[HAL INFO] Enable channel=3
[HAL INFO] channel=0, period_ns=1000000, duty_ns=940000
[HAL INFO] channel=0, period=40000, duty=37600, polarity=0
[HAL INFO] Enable channel=0
===========================
PWM(1) Start
PWM(11) Start
PWM(0) Start
duty: 95
[HAL INFO] channel=1, period_ns=1000000, duty_ns=950000
[HAL INFO] channel=1, period=40000, duty=38000, polarity=0
[HAL INFO] Enable channel=1
[HAL INFO] channel=3, period_ns=1000000, duty_ns=950000
[HAL INFO] channel=3, period=40000, duty=38000, polarity=0
[HAL INFO] Enable channel=3
[HAL INFO] channel=0, period_ns=1000000, duty_ns=950000
[HAL INFO] channel=0, period=40000, duty=38000, polarity=0
[HAL INFO] Enable channel=0
===========================
PWM(1) Start
PWM(11) Start
PWM(0) Start
duty: 96
[HAL INFO] channel=1, period_ns=1000000, duty_ns=960000
[HAL INFO] channel=1, period=40000, duty=38400, polarity=0
[HAL INFO] Enable channel=1
[HAL INFO] channel=3, period_ns=1000000, duty_ns=960000
[HAL INFO] channel=3, period=40000, duty=38400, polarity=0
[HAL INFO] Enable channel=3
[HAL INFO] channel=0, period_ns=1000000, duty_ns=960000
[HAL INFO] channel=0, period=40000, duty=38400, polarity=0
[HAL INFO] Enable channel=0

...
```

