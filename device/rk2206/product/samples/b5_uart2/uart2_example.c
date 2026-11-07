/*
 * Copyright (c) 2024 iSoftStone Education Co., Ltd.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */
#include "los_task.h"
#include "ohos_init.h"
#include "iot_uart.h"
#include "iot_errno.h"

#define UART_ID                 EUART2_M1

// 字符串最大长度
#define STRING_MAXSIZE          128
// FiFo最大单位数量
#define FIFO_MAX_UNIT           1024
// FiFo结构体，用于存放串口接收
struct tagFifo
{
    int max;            // 缓冲区最大单元数目
    int read;           // 读操作的偏移位置
    int write;          // 写操作的偏移位置
    unsigned char buffer[FIFO_MAX_UNIT];
};
// 定义串口接收的Fifo缓冲区
static struct tagFifo m_uart_recv_fifo =
{
    .max = FIFO_MAX_UNIT,
    .read = 0,
    .write = 0,
};

/***************************************************************
* 函数名称: fifo_init
* 说    明: 初始化Fifo
* 参    数:
*       @fifo           fifo结构体变量
* 返 回 值: 无
***************************************************************/
static void fifo_init(struct tagFifo *fifo)
{
    fifo->max = FIFO_MAX_UNIT;
    fifo->read = fifo->write = 0;
}

/***************************************************************
* 函数名称: fifo_is_empty
* 说    明: 判断Fifo是否已空
* 参    数:
*       @fifo           fifo结构体变量
* 返 回 值: 返回1为已空，反之为未空
***************************************************************/
static int fifo_is_empty(struct tagFifo *fifo)
{
    if (fifo->write == fifo->read)
    {
        return 1;
    }
    else
    {
        return 0;
    }
}

/***************************************************************
* 函数名称: fifo_is_full
* 说    明: 判断Fifo是否已满
* 参    数:
*       @fifo           fifo结构体变量
* 返 回 值: 返回1为已满，反之为未满
***************************************************************/
static int fifo_is_full(struct tagFifo *fifo)
{
    int write_next = (fifo->write + 1) % (fifo->max);
    if (write_next == fifo->read)
    {
        return 1;
    }
    else
    {
        return 0;
    }
}

/***************************************************************
* 函数名称: fifo_valid_data
* 说    明: 获取fifo的有效数据长度
* 参    数:
*       @fifo           fifo结构体变量
* 返 回 值: 返回fifo的有效数据长度
***************************************************************/
static int fifo_valid_data(struct tagFifo *fifo)
{
    return ((fifo->max + fifo->write - fifo->read) % fifo->max);
}

/***************************************************************
* 函数名称: fifo_write
* 说    明: 将字符串写入fifo中
* 参    数:
*       @fifo           fifo结构体变量
*       @buffer         存放数据的缓存区
*       @buffer_length  缓冲区数据长度
* 返 回 值: 返回将buffer的数据存放到fifo，0表示无法写入fifo
***************************************************************/
static int fifo_write(struct tagFifo *fifo, unsigned char *buffer, int buffer_length)
{
    for (int i = 0; i < buffer_length; i++)
    {
        if (fifo_is_full(&fifo) != 0)
        {
            printf("%s, %d: fifo_write() fifo is full and loss data\n", __FILE__, __LINE__);
        }
        fifo->buffer[fifo->write] = buffer[i];
        fifo->write = (fifo->write + 1) % (fifo->max);
    }
}

/***************************************************************
* 函数名称: fifo_read
* 说    明: 从fifo中读取字符串
* 参    数:
*       @fifo       fifo结构体变量
*       @buffer     将从fifo读取数据，存放到该缓冲区中
*       @buffer_len 保存fifo读取数据的长度
* 返 回 值: 返回从fifo读取数据的长度，0表示没有数据
***************************************************************/
static int fifo_read(struct tagFifo *fifo, unsigned char *buffer, int buffer_maxlen, int *buffer_len)
{
    int valid_data_len = 0;
    
    for (int i = 0; i < buffer_maxlen; i++)
    {
        // 判断fifo不为空
        if (fifo_is_empty(fifo) == 0)
        {
            buffer[i] = fifo->buffer[fifo->read];
            fifo->read = (fifo->read + 1) % fifo->max;
            valid_data_len++;
            continue;
        }
        else
        {
            break;
        }
    }
    
    *buffer_len = valid_data_len;
    return valid_data_len;
}

/***************************************************************
* 函数名称: uart2_recv_process
* 说    明: 串口接收任务
* 参    数: 无
* 返 回 值: 无
* 注    意：串口接收任务不能有多余的延时操作，
*          否则会导致串口接收不到某部分字符串
***************************************************************/
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

/***************************************************************
* 函数名称: uart2_process
* 说    明: 负责串口初始化，创建串口接收任务，并每隔1秒发送串口信息
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void uart2_process(void)
{
    unsigned int ret;
    IotUartAttribute attr;
    unsigned int thread_id;
    TSK_INIT_PARAM_S task = {0};
    unsigned char send_buffer[STRING_MAXSIZE];
    unsigned char recv_buffer[STRING_MAXSIZE];
    int recv_length = 0;
    
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
    // 等待
    LOS_Msleep(1000);
    
    // 初始化串口
    fifo_init(&m_uart_recv_fifo);
    
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
    
    // 发送字符串初始化
    for (int i = 0; i < STRING_MAXSIZE; i++)
    {
        send_buffer[i] = i;
    }
    
    while (1)
    {
        printf("uart write and send_buffer_len = %d\n", STRING_MAXSIZE);
        // IoTUartWrite是异步发送，非阻塞发送
        IoTUartWrite(UART_ID, send_buffer, STRING_MAXSIZE);
        // 等待发送完毕
        LOS_Msleep(1000);
        
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
        
        LOS_Msleep(1000);
    }
    
    return;
}

/***************************************************************
* 函数名称: uart2_example
* 说    明: 开机自启动调用函数
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void uart2_example()
{
    unsigned int thread_id;
    TSK_INIT_PARAM_S task = {0};
    unsigned int ret = LOS_OK;
    
    task.pfnTaskEntry = (TSK_ENTRY_FUNC)uart2_process;
    task.uwStackSize = 1024 * 1024;
    task.pcName = "uart2 process";
    task.usTaskPrio = 24;
    ret = LOS_TaskCreate(&thread_id, &task);
    if (ret != LOS_OK)
    {
        printf("Falied to create task ret:0x%x\n", ret);
        return;
    }
}

APP_FEATURE_INIT(uart2_example);
