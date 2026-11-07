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
#include <string.h>
#include <unistd.h>
#include <math.h>
#include "los_task.h"
#include "ohos_init.h"
#include "iot_errno.h"
#include "iot_i2c.h"

/* i2c总线编号 */
#define I2C_BUS             EI2C0_M2

#define SLAVE_ADDRESS_MAXSIZE           0x100
/***************************************************************
* 函数名称: i2c_process
* 说    明: i2c扫描任务
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void i2c_scan_process()
{
    unsigned int ret = 0;
    unsigned short slaveAddr[SLAVE_ADDRESS_MAXSIZE];
    unsigned int slaveAddrLen;
    unsigned int i;
    
    /* 初始化i2c */
    ret = IoTI2cInit(I2C_BUS, EI2C_FRE_100K);
    if (ret != IOT_SUCCESS)
    {
        printf("I2c init fail!\r\n");
    }

    while(1)
    {
        printf("***************I2C Scan Example*************\r\n");

        /* 扫描挂载的i2c设备 */
        slaveAddrLen = IoTI2cScan(I2C_BUS, slaveAddr, SLAVE_ADDRESS_MAXSIZE);

        for (i = 0; i < slaveAddrLen; i++)
        {
            printf("slave address: 0x%02x\n", slaveAddr[i]);
        }

        printf("\r\n");

        LOS_Msleep(1000);
    }
}


/***************************************************************
* 函数名称: i2c_scan_example
* 说    明: 开机自启动调用函数
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void i2c_scan_example()
{
    unsigned int thread_id;
    TSK_INIT_PARAM_S task = {0};
    unsigned int ret = LOS_OK;

    task.pfnTaskEntry = (TSK_ENTRY_FUNC)i2c_scan_process;
    task.uwStackSize = 20480;
    task.pcName = "i2c scan process";
    task.usTaskPrio = 24;
    ret = LOS_TaskCreate(&thread_id, &task);
    if (ret != LOS_OK)
    {
        printf("Falied to create task ret:0x%x\n", ret);
        return;
    }
}

APP_FEATURE_INIT(i2c_scan_example);