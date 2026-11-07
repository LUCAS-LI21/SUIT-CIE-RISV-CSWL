# 通晓开发板基础外设开发——ADC

本示例将演示如何在通晓开发板上使用ADC做按键测试。

![通晓开发板-RK2206](/vendor/isoftstone/rk2206/docs/figures/tx_smart_r-rk2206.jpg)

## 实验设计

### 硬件设计

![按键原理图](/vendor/isoftstone/rk2206/docs/figures/adc/按键原理图.jpg)

![按键与开发板连接](/vendor/isoftstone/rk2206/docs/figures/adc/按键与开发板连接.jpg)

从原理图可以看出当按键按下后，不同阻值的电阻会对3.3v电压进行分压：

- 不按任何按键，adc值约为3.3v
- 按下K3，adc值约为10mv
- 按下K4，adc值约为1v
- 按下K5，adc值约为1.65v
- 按下K6，adc值约为0.55v

### 软件设计

#### 导入ADC头文件

```c
#include "iot_adc.h"
```

[adc接口文档](/device/rockchip/hardware/docs/ADC.md)

#### adc初始化源代码分析

这部分代码为adc初始化的代码。首先用 `IoTAdcInit()` 函数将 `GPIO0_PC7` 配置为adc。

```c
/***************************************************************
* 函数名称: adc_dev_init
* 说    明: 初始化ADC
* 参    数: 无
* 返 回 值: 0为成功，反之为失败
***************************************************************/
static unsigned int adc_dev_init()
{
    unsigned int ret = 0;

    /* 初始化ADC */
    ret = IoTAdcInit(KEY_ADC_CHANNEL);

    if(ret != IOT_SUCCESS)
    {
        printf("%s, %s, %d: ADC Init fail\n", __FILE__, __func__, __LINE__);
    }

    return 0;
}

/***************************************************************
* 函数名称: adc_process
* 说    明: ADC采集循环任务
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void adc_process()
{
    float voltage;

    /* 初始化adc设备 */
    adc_dev_init();
    
    ...
}
```

#### ADC读数据操作

ADC模块提供 `IoTAdcGetVal()`读取ADC数据。具体ADC读数据数据的操作如下：

```c
/* 获取ADC值 */
ret = IoTAdcGetVal(KEY_ADC_CHANNEL, &data);

if (ret != IOT_SUCCESS)
{
    printf("%s, %s, %d: ADC Read Fail\n", __FILE__, __func__, __LINE__);
    return 0.0;
}
```

#### ADC数据计算成实际电压操作

ADC模块采用10位的ADC采集寄存器，可测试电压范围为0~3.3V，所以ADC采集数值换算为实际电压计算公司为：

```r
实际电压 = (ADC采集数值 / 1024) * 3.3V
```

注意：实际电压是 `float`类型，源代码计算要规范。具体源代码如下所示：

```c
return (float)(data * 3.3 / 1024.0)
```

## 编译调试

### 修改 BUILD.gn 文件

修改 `vendor/isoftstone/rk2206/sample` 路径下 BUILD.gn 文件，指定 `adc_example` 参与编译。

```r
"./b3_adc:adc_example"",
```

修改 `device/rockchip/rk2206/sdk_liteos` 路径下 Makefile 文件，添加 `-ladc_example` 参与编译。

```r
hardware_LIBS = -lhal_iothardware -lhardware -ladc_example
```

### 运行结果

示例代码编译烧录代码后，按下开发板的RESET按键，通过串口助手查看日志，显示如下：

```r
***************Adc Example*************
vlt:3.297V
***************Adc Example*************
vlt:3.297V
***************Adc Example*************
vlt:3.297V
***************Adc Example*************
vlt:3.297V
***************Adc Example*************
vlt:3.297V
***************Adc Example*************
vlt:3.297V
```
