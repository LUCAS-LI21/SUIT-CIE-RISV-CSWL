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
#include "mpu6050.h"

/***************************************************************
 * 函数名称: mpu6050_proress
 * 说    明: mpu6050实验
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void mpu6050_proress(void *arg)
{
    short mpu6050_data[3] = {0};

    mpu6050_init();

    while (1)
    {
        printf("************ Mpu6050 Process ************\n");

        mpu6050_read_data(mpu6050_data);

        printf("x %d y %d z %d \r\n", mpu6050_data[0], mpu6050_data[1], mpu6050_data[2]);

        printf("\r\n");

        LOS_Msleep(1000);
    }
}

/***************************************************************
 * 函数名称: mpu6050_example
 * 说    明: 开机自启动调用函数
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void mpu6050_example()
{
    unsigned int thread_id;
    TSK_INIT_PARAM_S task = {0};
    unsigned int ret = LOS_OK;

    task.pfnTaskEntry = (TSK_ENTRY_FUNC)mpu6050_proress;
    task.uwStackSize = 2048;
    task.pcName = "mpu6050 process";
    task.usTaskPrio = 24;
    ret = LOS_TaskCreate(&thread_id, &task);
    if (ret != LOS_OK)
    {
        printf("Falied to create task ret:0x%x\n", ret);
        return;
    }
}

APP_FEATURE_INIT(mpu6050_example);
