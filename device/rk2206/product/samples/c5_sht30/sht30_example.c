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
#include "sht30.h"



float g_humi;
float g_temp;
/***************************************************************
 * 函数名称: sht30_proress
 * 说    明: sht30实验
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void sht30_proress(void *arg)
{
    double sht30_data[2] = {0};

    /* 初始化sht30 */
    sht30_init();

    while (1)
    {
        printf("************ Sht30 Process ************\n");

        /* 读取sht30数据 */
        sht30_read_data(sht30_data);

        printf("temperature %.2f RH %.2f \r\n", sht30_data[0], sht30_data[1]);

        g_temp = sht30_data[0];
        g_humi = sht30_data[1];

        printf("\r\n");

        LOS_Msleep(1000);
    }
}

/***************************************************************
 * 函数名称: sht30_example
 * 说    明: 开机自启动调用函数
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void sht30_example()
{
    unsigned int thread_id;
    TSK_INIT_PARAM_S task = {0};
    unsigned int ret = LOS_OK;

    task.pfnTaskEntry = (TSK_ENTRY_FUNC)sht30_proress;
    task.uwStackSize = 2048;
    task.pcName = "sht30 process";
    task.usTaskPrio = 24;
    ret = LOS_TaskCreate(&thread_id, &task);
    if (ret != LOS_OK)
    {
        printf("Falied to create task ret:0x%x\n", ret);
        return;
    }
}

APP_FEATURE_INIT(sht30_example);
