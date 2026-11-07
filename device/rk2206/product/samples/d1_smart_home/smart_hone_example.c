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
#include <stdbool.h>

#include "los_task.h"
#include "ohos_init.h"

#include "smart_home.h"

#define MSG_QUEUE_LENGTH 16
#define BUFFER_LEN 50

bool motor_state = false;
bool light_state = false;
bool auto_state = false;

static unsigned int m_msg_queue;
unsigned int m_su03_msg_queue;

/***************************************************************
 * 函数名称: smart_hone_thread
 * 说    明: 智慧家居主线程
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void smart_hone_thread(void *arg)
{
    double *data_ptr = NULL;

    double illumination_range = 50.0;
    double temperature_range = 35.0;
    double humidity_range = 80.0;

    lcd_dev_init();
    motor_dev_init();
    light_dev_init();
    su03t_init();

    lcd_load_ui();

    while (1)
    {
        LOS_QueueRead(m_msg_queue, (void *)&data_ptr, BUFFER_LEN, LOS_WAIT_FOREVER);

        if (data_ptr[0] < illumination_range && auto_state)
        {
            light_state = true;
            light_set_state(light_state);
        }
        else if (data_ptr[0] > illumination_range && auto_state)
        {
            light_state = false;
            light_set_state(light_state);
        }
        else if (!auto_state)
        {
            light_set_state(light_state);
        }

        if ((data_ptr[1] > temperature_range) && auto_state)
        {
            motor_state = true;
            motor_set_state(motor_state);
        }
        else if ((data_ptr[2] > humidity_range) && auto_state)
        {
            motor_state = true;
            motor_set_state(motor_state);
        }
        else if ((data_ptr[1] < temperature_range && data_ptr[2] < humidity_range) && auto_state)
        {
            motor_state = false;
            motor_set_state(motor_state);
        }
        else if (!auto_state)
        {
            motor_set_state(motor_state);
        }

        lcd_set_illumination(data_ptr[0]);
        lcd_set_temperature(data_ptr[1]);
        lcd_set_humidity(data_ptr[2]);
        lcd_set_light_state(light_state);
        lcd_set_motor_state(motor_state);
        lcd_set_auto_state(auto_state);

        // printf("============= smart home example ==============\n");
        // printf("======== data ========\r\n");
        // printf("illumination:%5.2f\r\n", data_ptr[0]);
        // printf("temperature:%5.2f\r\n", data_ptr[1]);
        // printf("humidity:%5.2f\r\n", data_ptr[2]);
        // printf("======== state ========\r\n");
        // printf("light_state:%d\r\n", light_state);
        // printf("motor_state:%d\r\n", motor_state);
        // printf("auto_state:%d\r\n", auto_state);

        // printf("\r\n");

        LOS_Msleep(1500);
    }
}

/***************************************************************
 * 函数名称: device_read_thraed
 * 说    明: 设备读取线程
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void device_read_thraed(void *arg)
{
    double read_data[3] = {0};

    i2c_dev_init();

    while (1)
    {
        bh1750_read_data(&read_data[0]);
        sht30_read_data(&read_data[1]);
        LOS_QueueWrite(m_msg_queue, (void *)&read_data, sizeof(read_data), LOS_WAIT_FOREVER);
        LOS_QueueWrite(m_su03_msg_queue, (void *)&read_data, sizeof(read_data), LOS_WAIT_FOREVER);
        LOS_Msleep(500);
    }
}

/***************************************************************
 * 函数名称: smart_hone_example
 * 说    明: 开机自启动调用函数
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void smart_hone_example()
{
    unsigned int thread_id_1;
    unsigned int thread_id_2;
    TSK_INIT_PARAM_S task_1 = {0};
    TSK_INIT_PARAM_S task_2 = {0};
    unsigned int ret = LOS_OK;

    ret = LOS_QueueCreate("queue", MSG_QUEUE_LENGTH, &m_msg_queue, 0, BUFFER_LEN);
    if (ret != LOS_OK)
    {
        printf("Falied to create Message Queue ret:0x%x\n", ret);
        return;
    }

    ret = LOS_QueueCreate("su03_queue", MSG_QUEUE_LENGTH, &m_su03_msg_queue, 0, BUFFER_LEN);
    if (ret != LOS_OK)
    {
        printf("Falied to create Message Queue ret:0x%x\n", ret);
        return;
    }

    task_1.pfnTaskEntry = (TSK_ENTRY_FUNC)smart_hone_thread;
    task_1.uwStackSize = 2048;
    task_1.pcName = "smart hone thread";
    task_1.usTaskPrio = 24;
    ret = LOS_TaskCreate(&thread_id_1, &task_1);
    if (ret != LOS_OK)
    {
        printf("Falied to create task ret:0x%x\n", ret);
        return;
    }

    task_2.pfnTaskEntry = (TSK_ENTRY_FUNC)device_read_thraed;
    task_2.uwStackSize = 2048;
    task_2.pcName = "device read thraed";
    task_2.usTaskPrio = 24;
    ret = LOS_TaskCreate(&thread_id_2, &task_2);
    if (ret != LOS_OK)
    {
        printf("Falied to create task ret:0x%x\n", ret);
        return;
    }
}

APP_FEATURE_INIT(smart_hone_example);
