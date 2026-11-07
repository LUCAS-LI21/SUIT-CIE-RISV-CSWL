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

// 宏定义用于创建RGB颜色值
#define RGB(r, g, b) (((r) << 16) | ((g) << 8) | (b))

#define GET_R(x) ((x>>16)&0xff)
#define GET_G(x) ((x>>8)&0xff)
#define GET_B(x) ((x>>0)&0xff)

#define CALC_DUTY(x)  (x*100.0/0xff)

// 常见颜色的RGB值
#define BLACK   RGB(0, 0, 0)
#define WHITE   RGB(255, 255, 255)
#define RED     RGB(255, 0, 0)
#define GREEN   RGB(0, 255, 0)
#define BLUE    RGB(0, 0, 255)
#define YELLOW  RGB(255, 255, 0)
#define CYAN    RGB(0, 255, 255)
#define MAGENTA RGB(255, 0, 255)

// 更多颜色
#define ORANGE  RGB(255, 165, 0)
#define PURPLE  RGB(128, 0, 128)
#define GRAY    RGB(128, 128, 128)
#define LIGHT_GRAY RGB(211, 211, 211)
#define DARK_GRAY RGB(169, 169, 169)
#define BROWN   RGB(165, 42, 42)
#define PINK    RGB(255, 192, 203)
#define TURQUOISE RGB(64, 224, 208)  

uint32_t color_list[]={BLACK,WHITE,RED,GREEN,BLUE,YELLOW,CYAN,MAGENTA,
    ORANGE,PURPLE,GRAY,LIGHT_GRAY,DARK_GRAY,BROWN,PINK,TURQUOISE};

//占空比整形,让占空比处于1~99之间
int duty_fix(int duty){

    if(duty> 99){
        duty = 99;
    }
    if(duty <1){
        duty =1;
    }


    return duty;
}
//定义是否有渐变效果,1:设置渐变,0:取消渐变
#define USE_SMOOTH 1
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
    int index = 1;
    

    while (1)
    {
        printf("===========================\n");

        printf("PWM(%d) Start\n", LED_R_PORT);
        printf("PWM(%d) Start\n", LED_G_PORT);
        printf("PWM(%d) Start\n", LED_B_PORT);
        printf("duty: %d\r\n", duty);
        

        int r_duty = CALC_DUTY(GET_R(color_list[index]));
        int g_duty = CALC_DUTY(GET_G(color_list[index]));
        int b_duty = CALC_DUTY(GET_B(color_list[index]));
    
#if USE_SMOOTH

        int last_r_duty =CALC_DUTY(GET_R(color_list[index-1]));
        int last_g_duty = CALC_DUTY(GET_G(color_list[index-1]));
        int last_b_duty = CALC_DUTY(GET_B(color_list[index-1]));

        int diffValue_r = r_duty - last_r_duty ;
        int diffValue_g = g_duty - last_g_duty ;
        int diffValue_b = b_duty - last_b_duty ;

        int step_r = diffValue_r/10;
        int step_g = diffValue_g/10;
        int step_b = diffValue_b/10;

        for(int j = 0;j<=10;j++){

            last_r_duty = last_r_duty + step_r;
            last_g_duty = last_g_duty + step_g;
            last_b_duty = last_b_duty + step_b;
            
            //duty值整形,区间1~99
            last_r_duty = duty_fix(last_r_duty);
            last_g_duty = duty_fix(last_g_duty);
            last_b_duty = duty_fix(last_b_duty);

            
            ret = IoTPwmStart(LED_R_PORT, last_r_duty, 1000);
            if (ret != 0) {
                printf("IoTPwmStart failed(%d)\n", LED_R_PORT);
                continue;
            }
        
            ret = IoTPwmStart(LED_G_PORT, last_g_duty, 1000);
            if (ret != 0) {
                printf("IoTPwmStart failed(%d)\n", LED_G_PORT);
                continue;
            }
        
            ret = IoTPwmStart(LED_B_PORT, last_b_duty, 1000);
            if (ret != 0) {
                printf("IoTPwmStart failed(%d)\n", LED_B_PORT);
                continue;
            }
            

            LOS_Msleep(20);
        }
#else

        //duty值整形,区间1~99
        r_duty = duty_fix(r_duty);
        g_duty = duty_fix(g_duty);
        b_duty = duty_fix(b_duty);

        /* 启动PWM */
        ret = IoTPwmStart(LED_R_PORT, r_duty, 1000);
        if (ret != 0) {
            printf("IoTPwmStart failed(%d)\n", LED_R_PORT);
            continue;
        }

        ret = IoTPwmStart(LED_G_PORT, g_duty, 1000);
        if (ret != 0) {
            printf("IoTPwmStart failed(%d)\n", LED_G_PORT);
            continue;
        }

        ret = IoTPwmStart(LED_B_PORT, b_duty, 1000);
        if (ret != 0) {
            printf("IoTPwmStart failed(%d)\n", LED_B_PORT);
            continue;
        }
        LOS_Msleep(200);
      
#endif    

        index++;
        int color_list_len = sizeof(color_list)/sizeof(uint32_t);
        if(index>=color_list_len){
            index = 1;
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
