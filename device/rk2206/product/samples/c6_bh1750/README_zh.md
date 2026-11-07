# 通晓开发板基础设备开发——I2C控制BH1750获取光照强度

本示例将演示如何在通晓开发板上使用I2C控制BH1750获取光照强度

![通晓开发板](/vendor/isoftstone/rk2206/docs/figures/tx_smart_r-rk2206.jpg)

## 实验设计

### 硬件设计

![bh1750原理图](/vendor/isoftstone/rk2206/docs/figures/bh1750/bh1750原理图.jpg)

![bh1750与开发板连接](/vendor/isoftstone/rk2206/docs/figures/bh1750/bh1750与开发板连接.jpg)

从原理图中可以看出sht30与开发板的I2C0(GPIO0_A1\GPIO0_A0)连接。

### 软件设计

#### bh1750初始化

这部分代码为i2c初始化的代码。调用用 `IoTI2cInit()` 初始化I2C0端口。

```c
ret = IoTI2cInit(BH1750_I2C_PORT, EI2C_FRE_400K);
if (ret != IOT_SUCCESS)
{
    printf("i2c init fail!\r\v");
}
```

![bh1750_cmd](/vendor/isoftstone/rk2206/docs/figures/bh1750/bh1750_cmd.png)

配置为连续H分辨率模式

```c
uint8_t send_data[1] = {0x10};
uint32_t send_len = 1;

IoTI2cWrite(BH1750_I2C_PORT, BH1750_I2C_ADDRESS, send_data, send_len);
```

#### bh1750数据读取

调用 `bh1750_read_data()` 读取数据。

```c
void bh1750_read_data(double *dat)
{
    uint8_t recv_data[2] = {0};
    uint32_t receive_len = 2;   

    IoTI2cRead(BH1750_I2C_PORT, BH1750_I2C_ADDRESS, recv_data, receive_len);
    *dat = (float)(((recv_data[0] << 8) + recv_data[1]) / 1.2);
}
```

## 编译调试

### 修改 BUILD.gn 文件

修改 `vendor/isoftstone/rk2206/sample` 路径下 BUILD.gn 文件，指定 `bh1750_example` 参与编译。

```r
"./c6_bh1750:bh1750_example",
```

修改 `device/rockchip/rk2206/sdk_liteos` 路径下 Makefile 文件，添加 `-lbh1750_example` 参与编译。

```r
hardware_LIBS = -lhal_iothardware -lhardware -lbh1750_example,
```

### 运行结果

示例代码编译烧录代码后，按下开发板的RESET按键，通过串口助手查看日志，显示如下：

```c
************ bh1750 Process ************
lux: 465.00

************ bh1750 Process ************
lux: 464.17

************ bh1750 Process ************
lux: 463.33

```
