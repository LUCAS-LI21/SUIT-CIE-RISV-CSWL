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

#include "iot_errno.h"
#include "iot_gpio.h"

/*报警灯GPIO */
#define GPIO_ALARM_LIGHT     GPIO0_PA5

/***************************************************************
* 函数名称: gpio_process
* 说    明: gpio任务
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void gpio_process()
{
    unsigned int cur = 0;

    /* 初始化GPIO */
    IoTGpioInit(GPIO_ALARM_LIGHT);
    
    while (1)
    {
        printf("***************GPIO Example*************\r\n");
        printf("Write GPIO\r\n");
        /* 设置GPIO为输出模式 */
        IoTGpioSetDir(GPIO_ALARM_LIGHT, IOT_GPIO_DIR_OUT);
        if (cur == 0)
        {
            /* 输出低电平 */
            IoTGpioSetOutputVal(GPIO_ALARM_LIGHT, IOT_GPIO_VALUE0);
            cur = 1;
        }
        else
        {
            /* 输出高电平 */
            IoTGpioSetOutputVal(GPIO_ALARM_LIGHT, IOT_GPIO_VALUE1);
            cur = 0;
        }

        /* 睡眠1秒 */
        LOS_Msleep(1000);

        printf("\r\n");
    }
}


/***************************************************************
* 函数名称: gpio_example
* 说    明: 开机自启动调用函数
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void gpio_example()
{
    unsigned int thread_id;
    TSK_INIT_PARAM_S task = {0};
    unsigned int ret = LOS_OK;

    task.pfnTaskEntry = (TSK_ENTRY_FUNC)gpio_process;
    task.uwStackSize = 2048;
    task.pcName = "gpio process";
    task.usTaskPrio = 24;
    ret = LOS_TaskCreate(&thread_id, &task);
    if (ret != LOS_OK)
    {
        printf("Falied to create task ret:0x%x\n", ret);
        return;
    }
}

APP_FEATURE_INIT(gpio_example);