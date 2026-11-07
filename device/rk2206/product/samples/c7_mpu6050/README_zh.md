# 通晓开发板基础设备开发——I2C控制MPU6050获取值

本示例将演示如何在通晓开发板上使用I2C控制MPU6050获取值

![通晓开发板](/vendor/isoftstone/rk2206/docs/figures/tx_smart_r-rk2206.jpg)

## 实验设计

### 硬件设计

![mpu6050原理图](/vendor/isoftstone/rk2206/docs/figures/mpu6050/mpu6050原理图.jpg)

![mpu6050与开发板连接](/vendor/isoftstone/rk2206/docs/figures/mpu6050/mpu6050与开发板连接.jpg)

从原理图中可以看出sht30与开发板的I2C0(GPIO0_A1\GPIO0_A0)连接。

### 软件设计

#### mpu6050初始化

这部分代码为i2c初始化的代码。调用用 `IoTI2cInit()` 初始化I2C0端口。

MPU6050_RA_PWR_MGMT_1电源管理寄存器，DEVICE_RESET复位唤醒传感器。

![MPU6050_RA_PWR_MGMT_1](/vendor/isoftstone/rk2206/docs/figures/mpu6050/MPU6050_RA_PWR_MGMT_1.png)

MPU6050_RA_INT_ENABLE中断使能寄存器，中断控制。

![MPU6050_RA_INT_ENABLE](/vendor/isoftstone/rk2206/docs/figures/mpu6050/MPU6050_RA_INT_ENABLE.png)

MPU6050_RA_USER_CTRL用户控制寄存器，关闭I2C主模式。

![MPU6050_RA_USER_CTRL](/vendor/isoftstone/rk2206/docs/figures/mpu6050/MPU6050_RA_USER_CTRL.png)

MPU6050_RA_FIFO_EN FIFO使能寄存器，关闭FIFO。

![MPU6050_RA_FIFO_EN](/vendor/isoftstone/rk2206/docs/figures/mpu6050/MPU6050_RA_FIFO_EN.png)

MPU6050_RA_INT_PIN_CFG INT管脚/旁路控制寄存器。

![MPU6050_RA_INT_PIN_CFG](/vendor/isoftstone/rk2206/docs/figures/mpu6050/MPU6050_RA_INT_PIN_CFG.png)

MPU6050_RA_CONFIG配置寄存器，配置为外部引脚采样，DLPF数字低通滤波器。

![MPU6050_RA_CONFIG](/vendor/isoftstone/rk2206/docs/figures/mpu6050/MPU6050_RA_CONFIG.png)

MPU6050_RA_ACCEL_CONFIG加速配置寄存器，配置加速度传感器量程和高通滤波器。

![MPU6050_RA_ACCEL_CONFIG](/vendor/isoftstone/rk2206/docs/figures/mpu6050/MPU6050_RA_ACCEL_CONFIG.png)

```c
IoTI2cInit(MPU6050_I2C_PORT, EI2C_FRE_100K);

/*在初始化之前要延时一段时间，若没有延时，则断电后再上电数据可能会出错*/
LOS_Msleep(1000);

mpu6050_write_reg(MPU6050_RA_PWR_MGMT_1, 0X80); // 复位MPU6050
LOS_Msleep(200);
mpu6050_write_reg(MPU6050_RA_PWR_MGMT_1, 0X00);   // 唤醒MPU6050
mpu6050_write_reg(MPU6050_RA_INT_ENABLE, 0X00);   // 关闭所有中断
mpu6050_write_reg(MPU6050_RA_USER_CTRL, 0X00);    // I2C主模式关闭
mpu6050_write_reg(MPU6050_RA_FIFO_EN, 0X00);      // 关闭FIFO
mpu6050_write_reg(MPU6050_RA_INT_PIN_CFG, 0X80);  // 中断的逻辑电平模式,设置为0，中断信号为高电；设置为1，中断信号为低电平时。
action_interrupt();                               // 运动中断
mpu6050_write_reg(MPU6050_RA_CONFIG, 0x04);       // 配置外部引脚采样和DLPF数字低通滤波器
mpu6050_write_reg(MPU6050_RA_ACCEL_CONFIG, 0x1C); // 加速度传感器量程和高通滤波器配置
mpu6050_write_reg(MPU6050_RA_INT_PIN_CFG, 0X1C);  // INT引脚低电平平时
mpu6050_write_reg(MPU6050_RA_INT_ENABLE, 0x40);   // 中断使能寄存器
```

#### mpu6050数据读取

调用 `mpu6050_read_data()` 读取数据。

```c
void mpu6050_read_data(short *dat)
{
    short accel[3];
    short temp;
    if (mpu6050_read_id() == 0)
    {
        while (1)
            ;
    }
    mpu6050_read_acc(accel);
    dat[0] = accel[0];
    dat[1] = accel[1];
    dat[2] = accel[2];
    LOS_Msleep(500);
}
```

MPU6050_ACC_OUT角速度数据寄存器，只读，包括X轴、Y轴和Z轴。

![MPU6050_ACC_OUT](/vendor/isoftstone/rk2206/docs/figures/mpu6050/MPU6050_ACC_OUT.png)

```c
static void mpu6050_read_acc(short *acc_data)
{
    uint8_t buf[6];
    mpu6050_read_register(MPU6050_ACC_OUT, buf, 6);
    acc_data[0] = (buf[0] << 8) | buf[1];
    acc_data[1] = (buf[2] << 8) | buf[3];
    acc_data[2] = (buf[4] << 8) | buf[5];
}
```

## 编译调试

### 修改 BUILD.gn 文件

修改 `vendor/isoftstone/rk2206/sample` 路径下 BUILD.gn 文件，指定 `mpu6050_example` 参与编译。

```r
"./c7_mpu6050:mpu6050_example",
```

修改 `device/rockchip/rk2206/sdk_liteos` 路径下 Makefile 文件，添加 `-lmpu6050_example` 参与编译。

```r
hardware_LIBS = -lhal_iothardware -lhardware -lmpu6050_example,
```

### 运行结果

示例代码编译烧录代码后，按下开发板的RESET按键，通过串口助手查看日志，显示如下：

```c
************ Mpu6050 Process ************
x 16 y -2 z 1377

************ Mpu6050 Process ************
x 31 y 0 z 2185

************ Mpu6050 Process ************
x 31 y -3 z 2189

************ Mpu6050 Process ************
x 28 y -3 z 2189

************ Mpu6050 Process ************
x 28 y -3 z 2183


```
