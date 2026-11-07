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

/* RGB对应的PWM通道 */
#define LED_R_PORT EPWMDEV_PWM1_M1
#define LED_G_PORT EPWMDEV_PWM7_M1
#define LED_B_PORT EPWMDEV_PWM0_M1

void rgb_led_process()
{
    unsigned int ret;
    unsigned int duty = 1;
    unsigned int toggle = 0;

    /* 初始化PWM */
    ret = IoTPwmInit(LED_R_PORT);
    if (ret != 0) {
        printf("IoTPwmInit failed(%d)\n", LED_R_PORT);
    }

    ret = IoTPwmInit(LED_G_PORT);
    if (ret != 0) {
        printf("IoTPwmInit failed(%d)\n", LED_G_PORT);
    }

    ret = IoTPwmInit(LED_B_PORT);
    if (ret != 0) {
        printf("IoTPwmInit failed(%d)\n", LED_B_PORT);
    }
   

    while (1)
    {
        printf("===========================\n");

        printf("PWM(%d) Start\n", LED_R_PORT);
        printf("PWM(%d) Start\n", LED_G_PORT);
        printf("PWM(%d) Start\n", LED_B_PORT);
        printf("duty: %d\r\n", duty);
        
        /* 启动PWM */
        ret = IoTPwmStart(LED_R_PORT, duty, 1000);
        if (ret != 0) {
            printf("IoTPwmStart failed(%d)\n", LED_R_PORT);
            continue;
        }

        ret = IoTPwmStart(LED_G_PORT, duty, 1000);
        if (ret != 0) {
            printf("IoTPwmStart failed(%d)\n", LED_G_PORT);
            continue;
        }

        ret = IoTPwmStart(LED_B_PORT, duty, 1000);
        if (ret != 0) {
            printf("IoTPwmStart failed(%d)\n", LED_B_PORT);
            continue;
        }
        LOS_Msleep(200);

        /* 占空比由1~99 当到99时翻转 由99~1*/
        if (toggle)
        {
            duty--;
        }
        else
        {
            duty++;
        }
        
        if (duty == 99)
        {
            toggle = 1;
        }
        else if (duty == 1)
        {
            toggle = 0;
        }
        
    }
}
  
/***************************************************************
* 函数名称: rgb_led_example
* 说    明: rgb控制入口函数
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void rgb_led_example()
{
    unsigned int thread_id;
    TSK_INIT_PARAM_S task = {0};
    unsigned int ret = LOS_OK;

    task.pfnTaskEntry = (TSK_ENTRY_FUNC)rgb_led_process;
    task.uwStackSize = 2048;
    task.pcName = "rgb_led_process";
    task.usTaskPrio = 20;
    ret = LOS_TaskCreate(&thread_id, &task);
    if (ret != LOS_OK)
    {
        printf("Falied to create rgb_led_process ret:0x%x\n", ret);
        return;
    }
}

APP_FEATURE_INIT(rgb_led_example);
