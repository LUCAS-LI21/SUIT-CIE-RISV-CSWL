# 通晓开发板基础外设开发——GPIO中断

本示例将演示如何在通晓开发板上使用GPIO做输入输出操作。

![通晓开发板-RK2206](/vendor/isoftstone/rk2206/docs/figures/tx_smart_r-rk2206.jpg)

## 实验设计

### 硬件设计

![人体感应传感器原理图](/vendor/isoftstone/rk2206/docs/figures/gpio_interrupt/人体感应传感器原理图.jpg)

![人体感应与开发板连接](/vendor/isoftstone/rk2206/docs/figures/gpio_interrupt/人体感应与开发板连接.jpg)

从硬件原理图可以知道，GPIO0_A4与传感器连接。
- 人体感应传感器检测到人体，会由低电平上升为高电平。

### 软件设计

#### 导入GPIO头文件

```c
#include "iot_gpio.h"
```

[gpio接口文档](/device/rockchip/hardware/docs/GPIO.md)

#### GPIO初始化

这部分代码为GPIO初始化的代码。首先用 `IoTGpioInit()` 函数将 `GPIO0_PA3`配置成GPIO，然后调用`IoTGpioSetDir()`将GPIO配置成输入模式。

```c
void gpio_process()
{
    unsigned int ret;

    /* 初始化引脚为GPIO */
    IoTGpioInit(GPIO_BODY_INDUCTION);
    /* 引脚配置为输入 */
    IoTGpioSetDir(GPIO_BODY_INDUCTION, IOT_GPIO_DIR_IN);
    ...
}
```

#### GPIO配置中断

这部分代码为GPIO设置输出。首先用`IoTGpioRegisterIsrFunc()`函数将GPIO配置为中断触发模式，然后调用`IoTGpioSetIsrMask(GPIO_BODY_INDUCTION, TRUE)`函数使能中断触发。

```c
    /* 设置中断触发方式为上升沿和中断处理函数 */
    ret = IoTGpioRegisterIsrFunc(GPIO_BODY_INDUCTION, IOT_INT_TYPE_EDGE, IOT_GPIO_EDGE_RISE_LEVEL_HIGH, gpio_isr_func, NULL);
    if (ret != IOT_SUCCESS)
    {
        printf("IoTGpioRegisterIsrFunc failed(%d)\n", ret);
        return;
    }

    /* 初始化中断触发次数 */
    m_gpio_interrupt_count = 0;
    /* 关闭中断屏蔽 */
    IoTGpioSetIsrMask(GPIO_BODY_INDUCTION, FALSE);
```

#### GPIO中断响应函数

中断响应函数`gpio_isr_func()`主要响应GPIO中断触发，并将中断触发次数累加1，代码如下所示：

```c
void gpio_isr_func(void *args)
{
    m_gpio_interrupt_count++;
}
```

#### GPIO中断响应效果呈现

效果呈现如下：

```c
	while (1)
    {
        printf("***************GPIO Interrupt Example*************\n");
        printf("gpio interrupt count = %d\n", m_gpio_interrupt_count);
        printf("\n");
        
        /* 睡眠1秒 */
        LOS_Msleep(1000);
    }
```

## 编译调试

### 修改 BUILD.gn 文件

修改 `vendor/isoftstone/rk2206/sample` 路径下 BUILD.gn 文件，指定 `gpio_interrupt_example` 参与编译。

```r
"./b2_gpio_interrupt:gpio_interrupt_example",
```

修改 `device/rockchip/rk2206/sdk_liteos` 路径下 Makefile 文件，添加 `-lgpio_interrupt_example` 参与编译。

```r
hardware_LIBS = -lhal_iothardware -lhardware -lgpio_interrupt_example
```

### 运行结果

示例代码编译烧录代码后，按下开发板的RESET按键，当人靠近传感器时，m_gpio_interrupt_count计数值加一。通过串口助手查看日志，显示如下：

```r
***************GPIO Interrupt Example*************
gpio interrupt count = 0

***************GPIO Interrupt Example*************
gpio interrupt count = 0

***************GPIO Interrupt Example*************
gpio interrupt count = 0

***************GPIO Interrupt Example*************
gpio interrupt count = 0

IotProcess: sleep 5 sec!
***************GPIO Interrupt Example*************
gpio interrupt count = 1

***************GPIO Interrupt Example*************
gpio interrupt count = 1

***************GPIO Interrupt Example*************
gpio interrupt count = 1

***************GPIO Interrupt Example*************
gpio interrupt count = 1

***************GPIO Interrupt Example*************
gpio interrupt count = 2

IotProcess: sleep 5 sec!
***************GPIO Interrupt Example*************
gpio interrupt count = 2

***************GPIO Interrupt Example*************
gpio interrupt count = 2

***************GPIO Interrupt Example*************
gpio interrupt count = 2

***************GPIO Interrupt Example*************
gpio interrupt count = 2


......
```

