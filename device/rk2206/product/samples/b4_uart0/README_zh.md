# 通晓开发板基础外设开发——串口打印显示

本示例将演示如何在通晓开发板上使用额外串口打印

![通晓开发板](/vendor/isoftstone/rk2206/docs/figures/tx_smart_r-rk2206.jpg)

## 实验设计

### 硬件接口说明

引脚名称开发者可在硬件资源图中查看。

| 引脚名称 | 功能描述 |
| :--- | :------- | 
| GPIO0_PB6 | UART0_RX_M0 串口RX |
| GPIO0_PB7 | UART0_TX_M0 串口TX | 
| GND | 电源地引脚 | 

### 软件设计

#### 导入UART头文件

```c
#include "iot_uart.h"
```

[uart接口文档](/device/rockchip/hardware/docs/UART.md)

### 主要代码分析

串口0引脚GPIO0_PB6和GPIO0_PB7对应EUART0_M0。

```c
/* 定义UART设备数量 */
enum EnumUartId {
  EUART1_M1 = 0, /* RX GPIO0_PA6 TX GPIO0_PA7 */
  EUART2_M1,     /* RX GPIO0_PB2 TX GPIO0_PB3 */
  EUART0_M0,     /* RX GPIO0_PB6 TX GPIO0_PB7 */
  EUART1_M0,     /* RX GPIO0_PC2 TX GPIO0_PC3 */
  EUART0_M1,     /* RX GPIO0_PC6 TX GPIO0_PC7 */
  EUARTDEV_MAX
};

#define UART_ID EUART0_M0
```

这部分代码为uart初始化的代码。首先用 `IoTUartDeinit()` 函数将串口释放掉；其次用`IoTUartInit()`将串口初始化。

```c
IotUartAttribute attr;

IoTUartDeinit(UART_ID);

attr.baudRate = 115200;
attr.dataBits = IOT_UART_DATA_BIT_8;
attr.pad = IOT_FLOW_CTRL_NONE;
attr.parity = IOT_UART_PARITY_NONE;
attr.rxBlock = IOT_UART_BLOCK_STATE_NONE_BLOCK;
attr.stopBits = IOT_UART_STOP_BIT_1;
attr.txBlock = IOT_UART_BLOCK_STATE_NONE_BLOCK;

/* 初始化串口 */
ret = IoTUartInit(UART_ID, &attr);
if (ret != IOT_SUCCESS)
{
    printf("%s, %d: IoTUartInit(%d) failed!\n", __FILE__, __LINE__, ret);
    return;
}
/* 休眠1秒 */
LOS_Msleep(1000);
```

具体uart写操作如下：

```c
// IoTUartWrite是异步发送，非阻塞发送
IoTUartWrite(UART_ID, str, strlen(str));
// 等待发送完毕
LOS_Msleep(1000);
```

具体uart读操作如下：

```c
recv_length = 0;
memset(recv_buffer, 0, sizeof(recv_buffer));
recv_length = IoTUartRead(UART_ID, recv_buffer, sizeof(recv_buffer));
printf("%s, %d: uart recv and str(%s), len(%d)\n", __FILE__, __LINE__, recv_buffer, recv_length);
/* 休眠1秒 */
LOS_Msleep(1000);
```

## 编译调试

### 修改 BUILD.gn 文件

修改 `vendor\isoftstone\rk2206\sample` 路径下 BUILD.gn 文件，指定 `uart_example` 参与编译。

```r
"./b4_uart0:uart_example",
```

修改 `device/rockchip/rk2206/sdk_liteos` 路径下 Makefile 文件，添加 `-luart_example` 参与编译。

```r
hardware_LIBS = -lhal_iothardware -lhardware -luart_example
```

### 运行结果

示例代码编译烧录代码后，按下开发板的RESET按键，通过串口助手查看日志，串口0显示如下：

```c
HelloWorld!
HelloWorld!
HelloWorld!
HelloWorld!
...
```

注意：如果你使用MobaXterm，并且发现串口0能打印，但是换行有问题，则：
（1）在MobaXterm界面中按住右边的Ctrl按键 + 右击鼠标，弹出菜单，选择`Change terminal settings...`，如下图所示：

![右击菜单图](/vendor/isoftstone/rk2206/docs/figures/uart0/MobaXterm_右击菜单.png)

（2）将2个选项勾选上即可。如下图所示：

![勾选图](/vendor/isoftstone/rk2206/docs/figures/uart0/MobaXterm_勾选.png)

## 注意事项

（1）RK2206的串口0波特率误差率为3%。一般而言，市面上的USB转串口（如：CH430等）的允许误差率为0.3%，会导致通晓开发板的串口发送数据出现乱码（其实不是乱码，而是波特率有误差，导致出现乱码），请替换为接收允许误差率为3%的USB转串口芯片，比如FS232。
