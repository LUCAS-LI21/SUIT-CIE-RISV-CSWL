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

#include "smart_security.h"
#include "su_03t.h"

#define MSG_QUEUE_LENGTH                                16
#define BUFFER_LEN                                      50

static unsigned int m_msg_queue;
unsigned int m_su03_msg_queue;

bool alarm_light_state = false;
bool beep_state = false;
bool auto_state = true;

/***************************************************************
 * 函数名称: smart_security_thread
 * 说    明: 智慧安防主线程
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void smart_security_thread(void *arg)
{
    double *data_ptr = NULL;

    double ppm_range = 800.0;

    bool light_last_state = false;
    bool beep_last_state = false;

    beep_dev_init();
    alarm_light_init();
    lcd_dev_init();
    lcd_load_ui();
    su03t_init();

    while(1)
    {
        LOS_QueueRead(m_msg_queue, (void *)&data_ptr, BUFFER_LEN, LOS_WAIT_FOREVER);

        if ((data_ptr[0] > ppm_range || (bool)data_ptr[1]) && auto_state)
        {
            alarm_light_state = true;
            beep_state = true;
            beep_set_state(true);
            alarm_light_set_gpio(true);

            if (beep_state == beep_last_state)
            {
                continue;
            }

            if (alarm_light_state == light_last_state)
            {
                continue;
            }

            if (data_ptr[0] > ppm_range)
            {
                su03t_send_u8_msg(2, 0);
            }
            else if ((bool)data_ptr[1])
            {
                su03t_send_u8_msg(1, 0);
            }

            light_last_state = alarm_light_state;
            beep_last_state = beep_state;
        }
        else if ((data_ptr[0] < ppm_range || !(bool)data_ptr[1]) && auto_state)
        {
            alarm_light_state = false;
            beep_state = false;
            beep_set_state(false);
            alarm_light_set_gpio(false);
            light_last_state = alarm_light_state;
            beep_last_state = beep_state;
        }
        else if(!auto_state)
        {
            beep_set_state(beep_state);
            alarm_light_set_gpio(alarm_light_state);
        }

        lcd_set_ppm(data_ptr[0]);
        lcd_set_body_induction((bool)data_ptr[1]);
        lcd_set_beep_state(beep_state);
        lcd_set_alarm_light_state(alarm_light_state);
        lcd_set_auto_state(auto_state);

        printf("============= smart security example ==============\n");
        printf("======== data ========\r\n");
        printf("ppm:%5.2f\r\n", data_ptr[0]);
        printf("body_induction:%d\r\n", (bool)data_ptr[1]);
        printf("======== state ========\r\n");
        printf("beep_state:%d\r\n", beep_state);
        printf("alarm_light_state:%d\r\n", alarm_light_state);
        printf("auto_state:%d\r\n", auto_state);

        printf("\r\n");

        LOS_Msleep(500);
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
    double read_data[2] = {0};

    mq2_init();
    body_induction_dev_init();

    while(1)
    {
        mq2_read_data(&read_data[0]);
        body_induction_get_state((bool *)&read_data[1]);
        LOS_QueueWrite(m_msg_queue, (void *)&read_data, sizeof(read_data), LOS_WAIT_FOREVER);
        LOS_QueueWrite(m_su03_msg_queue, (void *)&read_data, sizeof(read_data), LOS_WAIT_FOREVER);
        LOS_Msleep(500);
    }
}

/***************************************************************
 * 函数名称: smart_security_example
 * 说    明: 开机自启动调用函数
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void smart_security_example()
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

    task_1.pfnTaskEntry = (TSK_ENTRY_FUNC)smart_security_thread;
    task_1.uwStackSize = 2048;
    task_1.pcName = "smart security thread";
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

APP_FEATURE_INIT(smart_security_example);
