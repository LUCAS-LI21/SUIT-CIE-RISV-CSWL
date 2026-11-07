# 通晓开发板基础外设开发——i2c扫描从设备

本示例将演示如何在通晓开发板上使用i2c做i2c从设备地址扫描操作。

![通晓开发板](/vendor/isoftstone/rk2206/docs/figures/tx_smart_r-rk2206.jpg)

## 实验设计

### 硬件设计

![i2c0](/vendor/isoftstone/rk2206/docs/figures/i2c_scan/i2c0.jpg)

挂载在i2c0上的设备：

![i2c0挂载设备1](/vendor/isoftstone/rk2206/docs/figures/i2c_scan/i2c0挂载设备1.jpg)

![i2c0挂载设备2](/vendor/isoftstone/rk2206/docs/figures/i2c_scan/i2c0挂载设备2.jpg)

![i2c0挂载设备3](/vendor/isoftstone/rk2206/docs/figures/i2c_scan/i2c0挂载设备3.jpg)

![i2c0挂载设备4](/vendor/isoftstone/rk2206/docs/figures/i2c_scan/i2c0挂载设备4.jpg)

地址分别为：

- eeprom：0x51
- bh1750：0x23
- sht30：0x44
- mpu6050：0x68

### 软件设计

#### 导入i2c头文件

```c
#include "iot_i2c.h"
```

[i2c接口文档](/device/rockchip/hardware/docs/I2C.md)

#### i2c初始化

i2c0使用了GPIO0_PA0和GPIO0_PA1引脚，所以对应的id为EI2C0_M2。

```c
/* 定义I2C设备数量 */
enum EnumI2cId {
  EI2C0_M2 = 0, /* SDA GPIO0_PA0 SCL GPIO0_PA1 */
  EI2C1_M2,     /* SDA GPIO0_PA2 SCL GPIO0_PA3 */
  EI2C0_M0,     /* SDA GPIO0_PB4 SCL GPIO0_PB5 */
  EI2C1_M0,     /* SDA GPIO0_PB6 SCL GPIO0_PB7 */
  EI2C1_M1,     /* SDA GPIO0_PC1 SCL GPIO0_PC2 */
  EI2C0_M1,     /* SDA GPIO0_PC6 SCL GPIO0_PC7 */
  EI2C2_M0,     /* SDA GPIO0_PD5 SCL GPIO0_PD6 */
  EI2CDEV_MAX
};

/* i2c总线编号 */
#define I2C_BUS             EI2C0_M2
```

这部分代码为i2c初始化的代码。使用 `IoTI2cInit()` 函数将 i2c总线初始化。

```c
/* 初始化i2c */
ret = IoTI2cInit(I2C_BUS, EI2C_FRE_100K);
if (ret != IOT_SUCCESS)
{
    printf("I2c init fail!\r\n");
}
```

#### 使用i2c扫描

这部分代码为使用i2c扫描函数。用`IoTI2cScan()`函数将从0x03到0x87轮流询问i2c总线挂载的从设备，如有反馈则记录该从设备地址到slaveAddr数组中。

```c
slaveAddrLen = IoTI2cScan(I2C_BUS, slaveAddr, SLAVE_ADDRESS_MAXSIZE);

for (i = 0; i < slaveAddrLen; i++)
{
    printf("slave address: 0x%02x\n", slaveAddr[i]);
}
```

### 修改 BUILD.gn 文件

修改 `vendor/isoftstone/rk2206/sample` 路径下 BUILD.gn 文件，指定 `i2c_scan_example` 参与编译。

```r
"./b6_i2c_scan:i2c_scan_example",
```

修改 `device/rockchip/rk2206/sdk_liteos` 路径下 Makefile 文件，添加 `-li2c_scan_example` 参与编译。

```r
app_LIBS = -li2c_scan_example
```

### 运行结果

示例代码编译烧录代码后，按下开发板的RESET按键，通过串口助手查看日志，显示如下：

```r
***************I2C Scan Example*************
slave address: 0x23
slave address: 0x44
slave address: 0x51
slave address: 0x68

......
```
