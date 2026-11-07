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
#include <stdio.h>
#include "los_task.h"
#include "ohos_init.h"
#include "iot_errno.h"
#include "iot_gpio.h"

/* 人体感应传感器GPIO */
#define GPIO_BODY_INDUCTION      GPIO0_PA3
#define GPIO_LED               GPIO0_PA5

/* 记录中断触发次数 */
static unsigned int m_gpio_interrupt_count = 0;
uint8_t light_flag= 0;

/***************************************************************
* 函数名称: gpio_isr_func
* 说    明: gpio中断响应处理函数
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void gpio_isr_func(void *args)
{
    printf("check Body!\n");
    // 发生人体感应,点亮LED
    IoTGpioSetOutputVal(GPIO_LED, IOT_GPIO_VALUE1);
    light_flag= 1;
}

/***************************************************************
* 函数名称: gpio_process
* 说    明: gpio任务
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void gpio_process()
{
    unsigned int ret;
   
    //初始化IO口
    IoTGpioInit(GPIO_LED);
    //设置GPIO为输出方向
    IoTGpioSetDir(GPIO_LED, IOT_GPIO_DIR_OUT);

    /* 初始化引脚为GPIO */
    IoTGpioInit(GPIO_BODY_INDUCTION);
    /* 引脚配置为输入 */
    IoTGpioSetDir(GPIO_BODY_INDUCTION, IOT_GPIO_DIR_IN);
    /* 设置中断触发方式为上升沿和中断处理函数 */
    ret = IoTGpioRegisterIsrFunc(GPIO_BODY_INDUCTION, 
        IOT_INT_TYPE_EDGE, IOT_GPIO_EDGE_RISE_LEVEL_HIGH, gpio_isr_func, NULL);
    if (ret != IOT_SUCCESS)
    {
        printf("IoTGpioRegisterIsrFunc failed(%d)\n", ret);
        return;
    }

    /* 初始化中断触发次数 */
    m_gpio_interrupt_count = 0;
    /* 关闭中断屏蔽 */
    IoTGpioSetIsrMask(GPIO_BODY_INDUCTION, FALSE);
   
    while (1)
    {
        printf("***************GPIO Interrupt Example*************\n");
        printf("gpio interrupt count = %d\n", m_gpio_interrupt_count);
        printf("\n");
        if(light_flag==1)
        {

            m_gpio_interrupt_count++;

            if(m_gpio_interrupt_count >=10)
            {
                //10s后关闭起夜灯,所有标志回复初始状态
                IoTGpioSetOutputVal(GPIO_LED, IOT_GPIO_VALUE0);
                m_gpio_interrupt_count= 0;
                light_flag= 0;
            }
            
        }
        
        /* 睡眠1秒 */
        LOS_Msleep(1000);
    }
}


/***************************************************************
* 函数名称: gpio_interrupt_example
* 说    明: 开机自启动调用函数
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void gpio_interrupt_example()
{
    unsigned int thread_id;
    TSK_INIT_PARAM_S task = {0};
    unsigned int ret = LOS_OK;

    task.pfnTaskEntry = (TSK_ENTRY_FUNC)gpio_process;
    task.uwStackSize = 20480;
    task.pcName = "gpio process";
    task.usTaskPrio = 24;
    ret = LOS_TaskCreate(&thread_id, &task);
    if (ret != LOS_OK)
    {
        printf("Falied to create task ret:0x%x\n", ret);
        return;
    }
}

APP_FEATURE_INIT(gpio_interrupt_example);