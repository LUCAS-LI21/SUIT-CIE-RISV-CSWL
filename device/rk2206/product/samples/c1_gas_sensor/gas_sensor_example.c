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
#include <stdio.h>

#include "mq2.h"

/***************************************************************
 * 函数名称: gas_sensor_process
 * 说    明: ADC采集循环任务
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void gas_sensor_process() 
{
  float ppm;

  /* 初始化mq2设备 */
  mq2_dev_init();

  LOS_Msleep(1000);

  /* 传感器校准 */
  mq2_ppm_calibration();

  while (1) 
  {
    printf("***************Gas Sensor Example*************\r\n");
    /*获取电压值*/
    ppm = get_mq2_ppm();
    printf("ppm: %.3f \r\n", ppm);

    /* 睡眠1秒 */
    LOS_Msleep(1000);
  }
}

/***************************************************************
 * 函数名称: gas_sensor_example
 * 说    明: 开机自启动调用函数
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void gas_sensor_example() 
{
  unsigned int thread_id;
  TSK_INIT_PARAM_S task = {0};
  unsigned int ret = LOS_OK;

  task.pfnTaskEntry = (TSK_ENTRY_FUNC)gas_sensor_process;
  task.uwStackSize = 2048;
  task.pcName = "gas_sensor process";
  task.usTaskPrio = 24;
  ret = LOS_TaskCreate(&thread_id, &task);
  if (ret != LOS_OK) 
  {
    printf("Falied to create task ret:0x%x\n", ret);
    return;
  }
}

APP_FEATURE_INIT(gas_sensor_example);