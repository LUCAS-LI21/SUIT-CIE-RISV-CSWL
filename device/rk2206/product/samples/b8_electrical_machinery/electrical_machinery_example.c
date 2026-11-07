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
#include "iot_pwm.h"

/* 电机对应PWM */
#define ELECTRICAL_MACHINERY_PORT EPWMDEV_PWM6_M0

/***************************************************************
* 函数名称: electrical_machinery_process
* 说    明: 控制电机线程函数
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void electrical_machinery_process()
{
    unsigned int ret;
    unsigned int duty = 10;

    /* 初始化PWM */
    ret = IoTPwmInit(ELECTRICAL_MACHINERY_PORT);
    if (ret != 0) {
        printf("IoTPwmInit failed(%d)\n", ELECTRICAL_MACHINERY_PORT);
    }

    while (1)
    {
        printf("===========================\n");

        printf("PWM(%d) Start\n", ELECTRICAL_MACHINERY_PORT);
        printf("duty: %d\r\n", duty);
        /* 开启PWM */
        ret = IoTPwmStart(ELECTRICAL_MACHINERY_PORT, duty, 1000);
        if (ret != 0) {
            printf("IoTPwmStart failed(%d)\n");
            continue;
        }
        
        LOS_Msleep(2000);

        /* 关闭PWM */
        ret = IoTPwmStop(ELECTRICAL_MACHINERY_PORT);
        if (ret != 0) {
            printf("IoTPwmStart failed(%d)\n");
            continue;
        }

        LOS_Msleep(2000);

        duty += 10;
        if (duty == 100)
        {
            duty = 10;
        }
    }
}

/***************************************************************
* 函数名称: electrical_machinery_example
* 说    明: 电机控制入口函数
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void electrical_machinery_example()
{
    unsigned int thread_id;
    TSK_INIT_PARAM_S task = {0};
    unsigned int ret = LOS_OK;

    task.pfnTaskEntry = (TSK_ENTRY_FUNC)electrical_machinery_process;
    task.uwStackSize = 2048;
    task.pcName = "electrical_machinery_process";
    task.usTaskPrio = 20;
    ret = LOS_TaskCreate(&thread_id, &task);
    if (ret != LOS_OK)
    {
        printf("Falied to create electrical_machinery_process ret:0x%x\n", ret);
        return;
    }
}

APP_FEATURE_INIT(electrical_machinery_example);
