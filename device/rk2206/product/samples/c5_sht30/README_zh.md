# 通晓开发板基础设备开发——I2C控制SHT30获取温湿度

本示例将演示如何在通晓开发板上使用I2C控制SHT30获取温湿度

![通晓开发板](/vendor/isoftstone/rk2206/docs/figures/tx_smart_r-rk2206.jpg)

## 实验设计

### 硬件设计

![eeprom原理图](/vendor/isoftstone/rk2206/docs/figures/sht30/sht30原理图.jpg)

![eeprom与开发板连接](/vendor/isoftstone/rk2206/docs/figures/sht30/sht30与开发板连接.jpg)

从原理图中可以看出sht30与开发板的I2C0(GPIO0_A1\GPIO0_A0)连接。

### 软件设计

#### sht30初始化

这部分代码为i2c初始化的代码。调用用 `IoTI2cInit()` 初始化I2C0端口。

```c
/* sht30对应i2c */
#define SHT30_I2C_PORT EI2C0_M2

ret = IoTI2cInit(SHT30_I2C_PORT, EI2C_FRE_400K);
if (ret != IOT_SUCCESS)
{
    printf("i2c init fail!\r\v");
}
```

设置测量周期，通过I2C总线下发测量周期命令0x2236，选择高重复性测量，每秒测量2次。

![sht30_meas_cmd](/vendor/isoftstone/rk2206/docs/figures/sht30/sht30_meas_cmd.png)

```c
IoTI2cWrite(SHT30_I2C_PORT, SHT30_I2C_ADDRESS, send_data, send_len); 
```

#### sht30数据读取

调用`sht30_read_data()`读取数据。

```c
void sht30_read_data(double *dat)
{
    /*checksum verification*/
    uint8_t data[3];
    uint16_t tmp;
    uint8_t rc;
    /*byte 0,1 is temperature byte 4,5 is humidity*/
    uint8_t SHT30_Data_Buffer[6];
    memset(SHT30_Data_Buffer, 0, 6);
    uint8_t send_data[2] = {0xE0, 0x00};

    uint32_t send_len = 2;
    IoTI2cWrite(SHT30_I2C_PORT, SHT30_I2C_ADDRESS, send_data, send_len);

    uint32_t receive_len = 6;
    IoTI2cRead(SHT30_I2C_PORT, SHT30_I2C_ADDRESS, SHT30_Data_Buffer, receive_len);

    /*check temperature*/
    data[0] = SHT30_Data_Buffer[0];
    data[1] = SHT30_Data_Buffer[1];
    data[2] = SHT30_Data_Buffer[2];
    rc = sht30_check_crc(data, 2, data[2]);
    if(!rc)
    {
        tmp = ((uint16_t)data[0] << 8) | data[1];
        dat[0] = sht30_calc_temperature(tmp);
    }
    
    /*check humidity*/
    data[0] = SHT30_Data_Buffer[3];
    data[1] = SHT30_Data_Buffer[4];
    data[2] = SHT30_Data_Buffer[5];
    rc = sht30_check_crc(data, 2, data[2]);
    if(!rc)
    {
        tmp = ((uint16_t)data[0] << 8) | data[1];
        dat[1] = sht30_calc_RH(tmp);
    }
}
```

温度转换：

![calc_temp](/vendor/isoftstone/rk2206/docs/figures/sht30/calc_temp.png)

```c
static float sht30_calc_temperature(uint16_t u16sT)
{
    float temperature = 0;

    /*clear bits [1..0] (status bits)*/
    u16sT &= ~0x0003;
    /*calculate temperature [℃]*/
    /*T = -45 + 175 * rawValue / (2^16-1)*/
    temperature = (175 * (float)u16sT / 65535 - 45);

    return temperature;
}
```

湿度转换：

![calc_lum](/vendor/isoftstone/rk2206/docs/figures/sht30/calc_lum.png)

```c
static float sht30_calc_RH(uint16_t u16sRH)
{
    float humidityRH = 0;

    /*clear bits [1..0] (status bits)*/
    u16sRH &= ~0x0003;
    /*calculate relative humidity [%RH]*/
    /*RH = rawValue / (2^16-1) * 10*/
    humidityRH = (100 * (float)u16sRH / 65535);

    return humidityRH;
}
```

## 编译调试

### 修改 BUILD.gn 文件

修改 `vendor/isoftstone/rk2206/sample` 路径下 BUILD.gn 文件，指定 `sht30_example` 参与编译。

```r
"./c5_sht30:sht30_example",
```

修改 `device/rockchip/rk2206/sdk_liteos` 路径下 Makefile 文件，添加 `-lsht30_example` 参与编译。

```r
hardware_LIBS = -lhal_iothardware -lhardware -lsht30_example,
```

### 运行结果

示例代码编译烧录代码后，按下开发板的RESET按键，通过串口助手查看日志，显示如下：

```c
************ Sht30 Process ************
temperature 32.40 RH 48.89

************ Sht30 Process ************
temperature 32.40 RH 48.90

************ Sht30 Process ************
temperature 32.42 RH 48.85

************ Sht30 Process ************
temperature 32.43 RH 48.83

```
