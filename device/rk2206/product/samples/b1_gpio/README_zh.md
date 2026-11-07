# 通晓开发板-基础外设开发——GPIO

本示例将演示如何在通晓开发板上使用GPIO做输入输出操作。

![通晓开发板-RK2206](/vendor/isoftstone/rk2206/docs/figures/tx_smart_r-rk2206.jpg)

## 实验设计

### 硬件设计

![报警灯原理图](/vendor/isoftstone/rk2206/docs/figures/gpio/报警灯原理图.jpg)

![报警灯与开发板连接](/vendor/isoftstone/rk2206/docs/figures/gpio/报警灯与开发板连接.jpg)

从硬件原理图可以知道：
- GPIO0_PA5引脚输出高电平时，三极管导通，LED亮。
- GPIO0_PA5引脚输出低电平时，三极管截止，LED灭。

### 软件设计

#### 导入GPIO头文件

```c
#include "iot_gpio.h"
```

[gpio接口文档](/device/rockchip/hardware/docs/GPIO.md)

#### GPIO初始化分析

这部分代码为GPIO初始化的代码。首先用 `IoTGpioInit()` 函数将 `GPIO0_PA5`初始化。

```c
void gpio_process()
{
    unsigned int cur = 0;
    IotGpioValue value = IOT_GPIO_VALUE0;

    /* 初始化GPIO */
    IoTGpioInit(GPIO_ALARM_LIGHT);
    ...
}
```

#### GPIO输出操作

这部分代码为GPIO设置输出。首先用`IoTGpioSetDir(GPIO_ALARM_LIGHT, IOT_GPIO_DIR_OUT)`函数将GPIO配置为输出模式，然后调用`IoTGpioSetOutputVal`函数输出GPIO电平值，最后调用`IoTGpioGetOutputVal(GPIO_TEST, &value)`读取输出GPIO电平值。

```c
printf("Write GPIO\r\n");
/* 设置GPIO为输出模式 */
IoTGpioSetDir(GPIO_ALARM_LIGHT, IOT_GPIO_DIR_OUT);
if (cur == 0)
{
        /* 输出低电平 */
        IoTGpioSetOutputVal(GPIO_ALARM_LIGHT, IOT_GPIO_VALUE0);
        /* 获取电平 */
        IoTGpioGetOutputVal(GPIO_ALARM_LIGHT, &value);
        printf("gpio set %d => gpio get %d\r\n", cur, value);

        cur = 1;
}
else
{
        /* 输出高电平 */
        IoTGpioSetOutputVal(GPIO_ALARM_LIGHT, IOT_GPIO_VALUE1);
        /* 获取电平 */
        IoTGpioGetOutputVal(GPIO_ALARM_LIGHT, &value);
        printf("gpio set %d => gpio get %d\r\n", cur, value);

        cur = 0;
}
/* 睡眠5秒 */
LOS_Msleep(5000);
```

#### GPIO输入操作

这部分代码为GPIO设置输出。首先用`IoTGpioSetDir(GPIO_ALARM_LIGHT, IOT_GPIO_DIR_IN)`函数将GPIO配置为输入模式，然后调用`IoTGpioGetInputVal(GPIO_TEST, &value)`函数读取输入GPIO电平值。

```c
printf("Read GPIO\r\n");
/* 设置GPIO为输出模式 */
IoTGpioSetDir(GPIO_ALARM_LIGHT, IOT_GPIO_DIR_IN);
/* 获取电平 */
IoTGpioGetInputVal(GPIO_ALARM_LIGHT, &value);
printf("gpio get %d\r\n", value);
/* 睡眠5秒 */
LOS_Msleep(5000);
```

## 编译调试

### 修改 BUILD.gn 文件

修改 `vendor/isoftstone/rk2206/sample` 路径下 BUILD.gn 文件，指定 `gpio_example` 参与编译。

```r
"./b1_gpio:gpio_example",
```

修改 `device/rockchip/rk2206/sdk_liteos` 路径下 Makefile 文件，添加 `-lgpio_example` 参与编译。

```r
hardware_LIBS = -lhal_iothardware -lhardware -lgpio_example
```

### 运行结果

示例代码编译烧录代码后，按下开发板的RESET按键，通过串口助手查看日志，显示如下：

```r
***************GPIO Example*************
Write GPIO
gpio set 0 => gpio get 0
IotProcess: sleep 5 sec!
Read GPIO
gpio get 0
IotProcess: sleep 5 sec!

......
```

