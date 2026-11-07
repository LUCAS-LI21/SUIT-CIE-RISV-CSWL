# 通晓开发板基础设备开发——NFC

本示例将演示如何在通晓开发板控制NFC

![通晓开发板](/vendor/isoftstone/rk2206/docs/figures/tx_smart_r-rk2206.jpg)

## 实验设计

### 硬件设计

![nfc硬件原理图](/vendor/isoftstone/rk2206/docs/figures/nfc/nfc硬件原理图.jpg)

![nfc硬件与开发板连接](/vendor/isoftstone/rk2206/docs/figures/nfc/nfc硬件与开发板连接.jpg)

从原理图中可以看到NFC芯片与开发板上的GPIO0_D5和GPIO0_D6连接。由于GPIO0_D5和GPIO0_D6是1.8V输出，所以使用了电压转换芯片。

### 软件设计

#### nfc_init()

```c
unsigned int nfc_init(void);
```

**描述：**

NFC模块初始化。

**参数：**

无

**返回值：**

0为成功，反之则失败。

#### nfc_deinit()

```c
unsigned int nfc_deinit(void);
```

**描述：**

NFC模块注销。

**参数：**

无

**返回值：**

0为成功，反之则失败。

#### nfc_store_uri_http()

```c
bool nfc_store_uri_http(RecordPosEnu position, uint8_t *http);
```

**描述：**

向NFC写入URI信息。

**参数：**

| 名字     | 描述                     |
| :------- | :----------------------- |
| position | 信息标识                 |
| http     | 需要写入的网络地址字符串 |

**返回值：**

true为成功，false则失败。

#### nfc_store_text()

```c
bool nfc_store_text(RecordPosEnu position, uint8_t *text);
```

**描述：**

向NFC写入txt信息。

**参数：**

| 名字     | 描述                 |
| :------- | :------------------- |
| position | 信息标识             |
| text     | 需要写入的内容字符串 |

**返回值：**

true为成功，false则失败。

### 主要代码分析

**初始化代码分析**

这部分代码为i2c初始化的代码。使用 `IoTI2cInit()` 函数初始化I2C1端口。

```c
if(IoTI2cInit(NFC_I2C_PORT, EI2C_FRE_400K) != IOT_SUCCESS) {
    printf("%s, %s, %d: I2c failed!\n", __FILE__, __func__, __LINE__);
    return IOT_FAILURE;
}
```

## 编译调试

### 修改 BUILD.gn 文件

修改 `vendor\isoftstone\rk2206\sample 路径下 BUILD.gn 文件，指定 `nfc` 参与编译。

```r
"./c4_nfc:nfc_example",
```

修改 `device/rockchip/rk2206/sdk_liteos` 路径下 Makefile 文件，添加 `-lnfc_example` 参与编译。

```r
hardware_LIBS = -lhal_iothardware -lhardware -lnfc_example
```

### 运行结果

示例代码编译烧录代码后，按下开发板的RESET按键，通过串口助手查看日志，显示如下：

```c
==============NFC Example==============
Please use the mobile phone with NFC function close to the development board!
```

