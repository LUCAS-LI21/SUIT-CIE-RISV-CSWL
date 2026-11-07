# 通晓开发板基础设备开发——mq2气体传感器

本示例将演示如何在通晓开发板上使用mq2气体传感器。

![通晓开发板-RK2206](/vendor/isoftstone/rk2206/docs/figures/tx_smart_r-rk2206.jpg)

## 实验设计

### 硬件设计

![气体传感器原理图](/vendor/isoftstone/rk2206/docs/figures/gas_sensor/气体传感器原理图.jpg)

![气体传感器与开发板连接](/vendor/isoftstone/rk2206/docs/figures/gas_sensor/气体传感器与开发板连接.jpg)

从原理图中可以看到mq2气体传感器与开发板的GPIO0_C4连接。GPIO0_C4对应ADC通道4。

### 软件设计

#### 初始化源代码分析

调用`mq2_dev_init()`初始化mq2设备。 

```c
/* 初始化mq2设备 */
mq2_dev_init();

LOS_Msleep(1000);
```

在`mq2_dev_init()`中调用了`IoTAdcInit()`对通道4的ADC进行初始化。

```c
#define MQ2_ADC_CHANNEL 4

unsigned int mq2_dev_init(void)
{
    unsigned int ret = 0;

    ret = IoTAdcInit(MQ2_ADC_CHANNEL);

    if(ret != IOT_SUCCESS)
    {
        printf("%s, %s, %d: ADC Init fail\n", __FILE__, __func__, __LINE__);
    }

    return 0;
}
```

#### 传感器校准

调用 `mq2_ppm_calibration()`获取传感器初始值，校准传感器。

```c
void mq2_ppm_calibration(void) 
{
  float voltage = adc_get_voltage();
  float rs = (5 - voltage) / voltage * RL;

  m_r0 = rs / powf(CAL_PPM / 613.9f, 1 / -2.074f);
}
```

#### 计算烟雾浓度

根据公式计算ppm值
阻值R与空气中被测气体的浓度C的计算关系式
`log R = mlog C + n (m，n均为常数)`
传感器的电阻计算
`Rs = (Vc/VRL-1) X RL`
Vc为回路电压，VRL是传感器4脚6脚输出电压，RL是负载。

```c
float get_mq2_ppm(void) 
{
  float voltage, rs, ppm;

  voltage = adc_get_voltage();
  rs = (5 - voltage) / voltage * RL;      // 计算rs
  ppm = 613.9f * powf(rs / m_r0, -2.074f); // 计算ppm
  return ppm;
}
```

## 编译调试

### 修改 BUILD.gn 文件

修改 `vendor/isoftstone/rk2206/sample` 路径下 BUILD.gn 文件，指定 `gas_sensor_example` 参与编译。

```r
"./c1_gas_sensor:gas_sensor_example",
```

修改 `device/rockchip/rk2206/sdk_liteos` 路径下 Makefile 文件，添加 `-lgas_sensor_example` 参与编译。

```r
hardware_LIBS = -lhal_iothardware -lhardware -lgas_sensor_example
```

### 运行结果

示例代码编译烧录代码后，按下开发板的RESET按键，通过串口助手查看日志，显示如下：

```r
***************Gas Sensor Example*************
ppm: 20.000
***************Gas Sensor Example*************
ppm: 19.177
***************Gas Sensor Example*************
ppm: 18.375
***************Gas Sensor Example*************
ppm: 17.981
***************Gas Sensor Example*************
ppm: 17.592
***************Gas Sensor Example*************
ppm: 16.829
***************Gas Sensor Example*************
ppm: 16.455
```
