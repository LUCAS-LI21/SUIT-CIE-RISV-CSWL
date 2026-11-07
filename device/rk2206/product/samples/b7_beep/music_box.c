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

static volatile int g_buttonPressed = 0;
static const uint16_t g_tuneFreqs[] = {
    0, // 160M Hz 对应的分频系数：
    4186,// 1046.5
    4700, // 1174.7
    5276, // 1318.5
    5588,// 1396.9
    6272,// 1568
    7040,// 1760
    7902,// 1975.5
    3136// 5_ 783.99 // 第一个八度的 5
};
// 曲谱音符
static const uint8_t g_scoreNotes[] = {
    // 《两只老虎》简谱
    1, 2, 3, 1,        1, 2, 3, 1,        3, 4, 5,  3, 4, 5,
    5, 6, 5, 4, 3, 1,  5, 6, 5, 4, 3, 1,  1, 8, 1,  1, 8, 1, 
};
// 曲谱时值
static const uint8_t g_scoreDurations[] = {
    4, 4, 4, 4,        4, 4, 4, 4,        4, 4, 8,  4, 4, 8,
    3, 1, 3, 1, 4, 4,  3, 1, 3, 1, 4, 4,  4, 4, 8,  4, 4, 8,
};

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
        printf("buzzerMusicTask start!\r\n");
        int count = sizeof(g_scoreNotes)/sizeof(g_scoreNotes[0]);
        for (size_t i = 0;i < count;i++) {
            // 音符
            uint32_t tune = g_scoreNotes[i]; 
            uint16_t freqDivisor = g_tuneFreqs[tune];
            // 音符时间
            uint32_t tuneInterval = g_scoreDurations[i] * (125*1000); 
            printf("%d %d %d %d\r\n", tune, 
                (160*1000*1000) / freqDivisor, freqDivisor, tuneInterval);
            IoTPwmStart(BEEP_PORT, 50, freqDivisor);
            usleep(tuneInterval);
            IoTPwmStop(BEEP_PORT);
            usleep(100000);
        }

       LOS_Msleep(2000);
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
