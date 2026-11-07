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

#ifndef __SMART_HOME_H__
#define __SMART_HOME_H__

#include <stdint.h>
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

enum motor_command
{
    motor_state_on = 0x0201,
    motor_state_off,
};

enum temperature_command
{
    temperature_get = 0x0301,
    humidity_get,
    illumination_get,
};

void i2c_dev_init(void);
void bh1750_read_data(double *dat);
void sht30_read_data(double *dat);

void light_dev_init(void);
void light_set_pwm(unsigned int duty);
void light_set_state(bool state);

void motor_dev_init(void);
void motor_set_pwm(unsigned int duty);
void motor_set_state(bool state);

void lcd_dev_init(void);
void lcd_load_ui(void);
void lcd_set_temperature(double temperature);
void lcd_set_humidity(double humidity);
void lcd_set_illumination(double illumination);
void lcd_set_light_state(bool state);
void lcd_set_motor_state(bool state);
void lcd_set_auto_state(bool state);

#endif
