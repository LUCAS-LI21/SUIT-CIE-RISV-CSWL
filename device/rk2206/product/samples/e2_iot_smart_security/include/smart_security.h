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

#ifndef __SMART_SECURITY_H__
#define __SMART_SECURITY_H__

#include <stdbool.h>

enum auto_command
{
    auto_state_on = 0x0001,
    auto_state_off,
};

enum light_command
{
    light_state_on = 0x0101,
    light_state_off,
};

enum beep_command
{
    beep_state_on = 0x0201,
    beep_state_off,
};

enum senror_command
{
    body_send = 0x0301,
    smoke_send,
};

void mq2_init(void);
void mq2_read_data(double *dat);

void beep_dev_init(void);
void beep_set_pwm(unsigned int duty);
void beep_set_state(bool state);

void alarm_light_init(void);
void alarm_light_set_gpio(bool state);

void body_induction_dev_init(void);
void body_induction_get_state(bool *dat);

void lcd_dev_init(void);
void lcd_load_ui(void);
void lcd_set_ppm(double ppm);
void lcd_set_body_induction(bool body_induction);
void lcd_set_beep_state(bool state);
void lcd_set_alarm_light_state(bool state);
void lcd_set_auto_state(bool state);

#endif
