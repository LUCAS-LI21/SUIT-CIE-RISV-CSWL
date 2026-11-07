# 通晓开发板基础外设开发——串口2收发数据

本示例将演示如何在通晓开发板上使用UART2收发数据。

注意：本样例专门创建任务，使用FiFo接收串口数据，是为了保证UART2接收到完整数据。

![通晓开发板](/vendor/isoftstone/rk2206/docs/figures/tx_smart_r-rk2206.jpg)

## 实验设计

### 硬件接口说明

引脚名称开发者可在硬件资源图中查看。

| 引脚名称 | 功能描述 |
| :--- | :------- |
| GPIO0_PB2 | UART2_RX_M1 串口RX |
| GPIO0_PB3 | UART2_TX_M1 串口TX |
| GND | 电源地引脚 | 

### 软件设计

#### 导入UART头文件

```c
#include "iot_uart.h"
```

[uart接口文档](/device/rockchip/hardware/docs/UART.md)

### 主要代码分析

#### 初始化串口

串口2引脚GPIO0_PB2和GPIO0_PB3对应EUART2_M1。

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

#define UART_ID EUART2_M1
```

这部分代码为uart初始化的代码。首先用 `IoTUartDeinit()` 函数将串口释放掉；其次用`IoTUartInit()`将串口初始化。

```c
IotUartAttribute attr;

// 串口注销
IoTUartDeinit(UART_ID);
// 串口配置
attr.baudRate = 115200;
attr.dataBits = IOT_UART_DATA_BIT_8;
attr.pad = IOT_FLOW_CTRL_NONE;
attr.parity = IOT_UART_PARITY_NONE;
attr.rxBlock = IOT_UART_BLOCK_STATE_NONE_BLOCK;
attr.stopBits = IOT_UART_STOP_BIT_1;
attr.txBlock = IOT_UART_BLOCK_STATE_NONE_BLOCK;

ret = IoTUartInit(UART_ID, &attr);
if (ret != IOT_SUCCESS)
{
    printf("%s, %d: IoTUartInit(%d) failed!\n", __FILE__, __LINE__, ret);
    return;
}
```

#### 串口写操作

具体uart写操作如下：

```c
printf("uart write and send_buffer_len = %d\n", STRING_MAXSIZE);
// IoTUartWrite是异步发送，非阻塞发送
IoTUartWrite(UART_ID, send_buffer, STRING_MAXSIZE);
// 等待发送完毕
LOS_Msleep(1000);
```

串口读操作

uart读操作实际上是从Fifo中读取数据，并非直接从uart读。

```c
printf("uart recv: \n");
for (int i = 0; i < 100; i++)
{
    recv_length = 0;
    memset(recv_buffer, 0, sizeof(recv_buffer));
    ret = fifo_read(&m_uart_recv_fifo, recv_buffer, sizeof(recv_buffer), &recv_length);
    if (ret > 0)
    {
        printf("	uart recv and i(%d), len(%d)\n", i, recv_length);
        for (int x = 0; x < recv_length; x++)
        {
            printf("		[%d] = 0x%x\n", x, recv_buffer[x]);
        }
        printf("");
    }
    else
    {
        printf("	uart recv and str is null\n");
        printf("");
        break;
    }
}
```

#### 创建串口接收任务

在主任务中创建串口接收任务，具体代码如下：

```c
// 创建接收任务
task.pfnTaskEntry = (TSK_ENTRY_FUNC)uart2_recv_process;
task.uwStackSize = 1024 * 1024;
task.pcName = "uart2 recv process";
task.usTaskPrio = 20;
ret = LOS_TaskCreate(&thread_id, &task);
if (ret != LOS_OK)
{
    printf("Falied to create task(uart2_recv_process) and ret: 0x%x\n", ret);
    return;
}
```

串口接收任务专门负责串口读操作，并将数据存入Fifo中，具体代码如下：

```c
void uart2_recv_process()
{
    unsigned char recv_buffer[STRING_MAXSIZE];
    int recv_length = 0;
    
    while (1)
    {
        recv_length = 0;
        memset(recv_buffer, 0, sizeof(recv_buffer));
        recv_length = IoTUartRead(UART_ID, recv_buffer, sizeof(recv_buffer));
        if (recv_length > 0)
        {
            // 写入到FiFo
            fifo_write(&m_uart_recv_fifo, recv_buffer, recv_length);
        }
        else
        {
            // 没有收到数据，则睡眠
            LOS_Msleep(1);
        }
    }
}
```

注意：串口接收任务不能有多余的延时操作，否则会导致串口接收不到某部分字符串。
## 编译调试

### 修改 BUILD.gn 文件

修改 `vendor\isoftstone\rk2206\sample` 路径下 BUILD.gn 文件，指定 `uart2_example` 参与编译。

```r
"./b5_uart2:uart2_example",
```

修改 `device/rockchip/rk2206/sdk_liteos` 路径下 Makefile 文件，添加 `-luart2_example` 参与编译。

```r
hardware_LIBS = -lhal_iothardware -lhardware -lm -luart2_example
```

### 运行结果

示例代码编译烧录代码后，按下开发板的RESET按键，通过串口助手查看日志，串口2显示如下：

```c
uart recv: 
        uart recv and str is null
uart write and send_buffer_len = 128
uart recv: 
        uart recv and str is null
uart write and send_buffer_len = 128
uart recv: 
        uart recv and str is null
uart write and send_buffer_len = 128
......
```

注意：如果你使用MobaXterm，并且发现串口2能打印，但是换行有问题，则：
（1）在MobaXterm界面中按住右边的Ctrl按键 + 右击鼠标，弹出菜单，选择`Change terminal settings...`，如下图所示：

![右击菜单图](/vendor/isoftstone/rk2206/docs/figures/uart2/MobaXterm_右击菜单.png)

（2）将2个选项勾选上即可。如下图所示：

![勾选图](/vendor/isoftstone/rk2206/docs/figures/uart2/MobaXterm_勾选.png)
