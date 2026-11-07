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

/* 蜂鸣器对应PWM */
#define BEEP_PORT EPWMDEV_PWM5_M0

/***************************************************************
* 函数名称: beep_process
* 说    明: 控制蜂鸣器线程函数
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void beep_process()
{
    unsigned int ret;
    unsigned int duty = 10;

    /* 初始化PWM */
    ret = IoTPwmInit(BEEP_PORT);
    if (ret != 0) {
        printf("IoTPwmInit failed(%d)\n", BEEP_PORT);
    }

    while (1)
    {
        printf("===========================\n");

        printf("PWM(%d) Start\n", BEEP_PORT);
        printf("duty: %d\r\n", duty);
        /* 开启PWM */
        ret = IoTPwmStart(BEEP_PORT, duty, 1000);
        if (ret != 0) {
            printf("IoTPwmStart failed(%d)\n");
            continue;
        }
        
        LOS_Msleep(2000);

        /* 关闭PWM */
        ret = IoTPwmStop(BEEP_PORT);
        if (ret != 0) {
            printf("IoTPwmStart failed(%d)\n");
            continue;
        }

        LOS_Msleep(2000);
        //占空比渐渐增大
        duty += 10;
        if (duty == 100)
        {
            duty = 10;
        }
    }
}

/***************************************************************
* 函数名称: beep_example
* 说    明: 蜂鸣器控制入口函数
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void beep_example()
{
    unsigned int thread_id;
    TSK_INIT_PARAM_S task = {0};
    unsigned int ret = LOS_OK;

    task.pfnTaskEntry = (TSK_ENTRY_FUNC)beep_process;
    task.uwStackSize = 2048;
    task.pcName = "beep_process";
    task.usTaskPrio = 20;
    ret = LOS_TaskCreate(&thread_id, &task);
    if (ret != LOS_OK)
    {
        printf("Falied to create beep_process ret:0x%x\n", ret);
        return;
    }
}

APP_FEATURE_INIT(beep_example);
