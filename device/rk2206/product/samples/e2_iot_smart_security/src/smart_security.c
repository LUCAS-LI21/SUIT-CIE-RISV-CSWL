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

#include "smart_security.h"

#include <stdbool.h>
#include <stdio.h>

#include "lcd.h"
#include "mq2.h"

#include "iot_pwm.h"
#include "iot_gpio.h"

#define BEEP_PORT EPWMDEV_PWM5_M0
#define GPIO_ALARM_LIGHT GPIO0_PA5
#define GPIO_BODY_INDUCTION GPIO0_PA3

static bool body_induction_state = false;

/***************************************************************
 * 函数名称: mq2_init
 * 说    明: mq2初始化
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void mq2_init(void)
{
    mq2_dev_init();
    mq2_ppm_calibration();
}

/***************************************************************
 * 函数名称: mq2_read_data
 * 说    明: 读取mq2传感器数据
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void mq2_read_data(double *dat)
{
    *dat = get_mq2_ppm();
}

/***************************************************************
 * 函数名称: beep_dev_init
 * 说    明: 蜂鸣器初始化
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void beep_dev_init(void)
{
    IoTPwmInit(BEEP_PORT);
}

/***************************************************************
 * 函数名称: beep_set_pwm
 * 说    明: 设置蜂鸣器PWM
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void beep_set_pwm(unsigned int duty)
{
    IoTPwmStart(BEEP_PORT, duty, 1000);
}

/***************************************************************
 * 函数名称: beep_set_state
 * 说    明: 设置蜂鸣器状态
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void beep_set_state(bool state)
{
    static bool last_state = false;

    if (state == last_state)
    {
        return;
    }

    if (state)
    {
        beep_set_pwm(20);
    }
    else
    {
        beep_set_pwm(1);
        IoTPwmStop(BEEP_PORT);
    }

    last_state = state;
}

/***************************************************************
 * 函数名称: alarm_light_init
 * 说    明: 报警灯初始化
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void alarm_light_init(void)
{
    IoTGpioInit(GPIO_ALARM_LIGHT);
    IoTGpioSetDir(GPIO_ALARM_LIGHT, IOT_GPIO_DIR_OUT);
}

/***************************************************************
 * 函数名称: alarm_light_set_gpio
 * 说    明: 设置报警灯
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void alarm_light_set_gpio(bool state)
{
    static bool last_state = false;

    if (state == last_state)
    {
        return;
    }

    if (state)
    {
        IoTGpioSetOutputVal(GPIO_ALARM_LIGHT, IOT_GPIO_VALUE1);
    }
    else
    {
        IoTGpioSetOutputVal(GPIO_ALARM_LIGHT, IOT_GPIO_VALUE0);
    }

    last_state = state;
}

/***************************************************************
 * 函数名称: body_induction_get_state
 * 说    明: 获取人体感应状态
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void body_induction_get_state(bool *dat)
{
    IotGpioValue value = IOT_GPIO_VALUE0;

    IoTGpioGetInputVal(GPIO_BODY_INDUCTION, &value);

    if (value) 
    {
        *dat = true;
    }
    else
    {
        *dat = false;
    }
}

/***************************************************************
 * 函数名称: body_induction_dev_init
 * 说    明: 人体感应传感器初始化
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void body_induction_dev_init(void)
{
    IoTGpioInit(GPIO_BODY_INDUCTION);
    IoTGpioSetDir(GPIO_BODY_INDUCTION, IOT_GPIO_DIR_IN);
}

/***************************************************************
 * 函数名称: lcd_dev_init
 * 说    明: lcd初始化
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void lcd_dev_init(void)
{
    lcd_init();
    lcd_fill(0, 0, LCD_W, LCD_H, LCD_WHITE);
}

/***************************************************************
 * 函数名称: lcd_load_ui
 * 说    明: lcd加载ui
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void lcd_load_ui(void)
{
    lcd_show_chinese(96, 0, "智慧安防", LCD_RED, LCD_WHITE, 32, 0);
    lcd_draw_line(0, 33, LCD_W, 33, LCD_BLACK);
    lcd_show_chinese(5, 34, "传感器数据", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_string(125, 34, ": ", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_chinese(5, 58, "烟雾浓度", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_string(221, 58, "ppm", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_chinese(5, 82, "人体感应", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_string(101, 82, ": ", LCD_RED, LCD_WHITE, 24, 0);
    lcd_draw_line(0, 131, LCD_W, 131, LCD_BLACK);
    lcd_show_chinese(5, 132, "设备状态", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_string(101, 132, ": ", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_chinese(5, 156, "蜂鸣器", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_string(77, 156, ": ", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_chinese(5, 180, "报警灯", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_string(77, 180, ": ", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_chinese(5, 204, "自动", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_string(53, 204, ": ", LCD_RED, LCD_WHITE, 24, 0);
}

/***************************************************************
 * 函数名称: lcd_set_ppm
 * 说    明: lcd设置烟雾浓度显示
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void lcd_set_ppm(double ppm)
{
    uint8_t buf[50] = {0};

    sprintf(buf, ": %.2f ", ppm);

    lcd_show_string(101, 58, buf, LCD_RED, LCD_WHITE, 24, 0);
}

/***************************************************************
 * 函数名称: lcd_set_body_induction
 * 说    明: lcd设置人体感应显示
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void lcd_set_body_induction(bool body_induction)
{
    if (body_induction)
    {
        lcd_show_chinese(125, 82, "有人", LCD_RED, LCD_WHITE, 24, 0);
    }
    else
    {
        lcd_show_chinese(125, 82, "无人", LCD_RED, LCD_WHITE, 24, 0);
    }
}

/***************************************************************
 * 函数名称: lcd_set_beep_state
 * 说    明: lcd设置蜂鸣器状态
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void lcd_set_beep_state(bool state)
{
    if (state)
    {
        lcd_show_chinese(101, 156, "开启", LCD_RED, LCD_WHITE, 24, 0);
    }
    else
    {
        lcd_show_chinese(101, 156, "关闭", LCD_RED, LCD_WHITE, 24, 0);
    }
}

/***************************************************************
 * 函数名称: lcd_set_alarm_light_state
 * 说    明: lcd设置警报灯状态
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void lcd_set_alarm_light_state(bool state)
{
    if (state)
    {
        lcd_show_chinese(101, 180, "开启", LCD_RED, LCD_WHITE, 24, 0);
    }
    else
    {
        lcd_show_chinese(101, 180, "关闭", LCD_RED, LCD_WHITE, 24, 0);
    }
}

/***************************************************************
 * 函数名称: lcd_set_auto_state
 * 说    明: lcd设置自动模式状态
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void lcd_set_auto_state(bool state)
{
    if (state)
    {
        lcd_show_chinese(101, 204, "开启", LCD_RED, LCD_WHITE, 24, 0);
    }
    else
    {
        lcd_show_chinese(101, 204, "关闭", LCD_RED, LCD_WHITE, 24, 0);
    }
}
