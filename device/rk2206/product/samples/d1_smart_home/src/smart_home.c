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

#include "smart_home.h"

#include <stdio.h>
#include <stdbool.h>
#include <stdint.h>

#include "iot_errno.h"
#include "iot_i2c.h"
#include "iot_pwm.h"
#include "iot_gpio.h"

#include "lcd.h"

#define I2C_HANDLE EI2C0_M2
#define MOTOR_PWM_HANDLE EPWMDEV_PWM6_M0
#define LED_R_GPIO_HANDLE GPIO0_PB5
#define LED_G_GPIO_HANDLE GPIO0_PB4
#define LED_B_GPIO_HANDLE GPIO1_PD0

#define SHT30_I2C_ADDRESS 0x44
#define BH1750_I2C_ADDRESS 0x23

/***************************************************************
 * 函数名称: sht30_init
 * 说    明: sht30初始化
 * 参    数: 无
 * 返 回 值: uint32_t IOT_SUCCESS表示成功 IOT_FAILURE表示失败
 ***************************************************************/
static uint32_t sht30_init(void)
{
    uint32_t ret = 0;
    uint8_t send_data[2] = {0x22, 0x36};
    uint32_t send_len = 2;

    ret = IoTI2cWrite(I2C_HANDLE, SHT30_I2C_ADDRESS, send_data, send_len); 
    if (ret != IOT_SUCCESS)
    {
        printf("I2c write failure.\r\n");
        return IOT_FAILURE;
    }

    return IOT_SUCCESS;
}

/***************************************************************
 * 函数名称: bh1750_init
 * 说    明: bh1750初始化
 * 参    数: 无
 * 返 回 值: uint32_t IOT_SUCCESS表示成功 IOT_FAILURE表示失败
 ***************************************************************/
static uint32_t bh1750_init(void)
{
    uint32_t ret = 0;
    uint8_t send_data[1] = {0x10};
    uint32_t send_len = 1;

    ret = IoTI2cWrite(I2C_HANDLE, SHT30_I2C_ADDRESS, send_data, send_len); 
    if (ret != IOT_SUCCESS)
    {
        printf("I2c write failure.\r\n");
        return IOT_FAILURE;
    }

    return IOT_SUCCESS;
}

/***************************************************************
* 函数名称: sht30_calc_RH
* 说    明: 湿度计算
* 参    数: u16sRH：读取到的湿度原始数据
* 返 回 值: 计算后的湿度数据
***************************************************************/
static float sht30_calc_RH(uint16_t u16sRH)
{
    float humidityRH = 0;

    /*clear bits [1..0] (status bits)*/
    u16sRH &= ~0x0003;
    /*calculate relative humidity [%RH]*/
    /*RH = rawValue / (2^16-1) * 10*/
    humidityRH = (100 * (float)u16sRH / 65535);

    return humidityRH;
}

/***************************************************************
* 函数名称: sht30_calc_temperature
* 说    明: 温度计算
* 参    数: u16sT：读取到的温度原始数据
* 返 回 值: 计算后的温度数据
***************************************************************/
static float sht30_calc_temperature(uint16_t u16sT)
{
    float temperature = 0;

    /*clear bits [1..0] (status bits)*/
    u16sT &= ~0x0003;
    /*calculate temperature [℃]*/
    /*T = -45 + 175 * rawValue / (2^16-1)*/
    temperature = (175 * (float)u16sT / 65535 - 45);

    return temperature;
}

/***************************************************************
* 函数名称: sht30_check_crc
* 说    明: 检查数据正确性
* 参    数: data：读取到的数据
            nbrOfBytes：需要校验的数量
            checksum：读取到的校对比验值
* 返 回 值: 校验结果，0-成功 1-失败
***************************************************************/
static uint8_t sht30_check_crc(uint8_t *data, uint8_t nbrOfBytes, uint8_t checksum)
{
    uint8_t crc = 0xFF;
    uint8_t bit = 0;
    uint8_t byteCtr ;
    const int16_t POLYNOMIAL = 0x131;

    /*calculates 8-Bit checksum with given polynomial*/
    for(byteCtr = 0; byteCtr < nbrOfBytes; ++byteCtr)
    {
        crc ^= (data[byteCtr]);
        for ( bit = 8; bit > 0; --bit)
        {
            if (crc & 0x80) crc = (crc << 1) ^ POLYNOMIAL;
            else crc = (crc << 1);
        }
    }

    if(crc != checksum)
        return 1;
    else
        return 0;
}

/***************************************************************
* 函数名称: sht30_read_data
* 说    明: 读取温度、湿度
* 参    数: dat：读取到的数据 0: 温度 1: 湿度
* 返 回 值: 无
***************************************************************/
void sht30_read_data(double *dat)
{
    /*checksum verification*/
    uint8_t data[3];
    uint16_t tmp;
    uint8_t rc;
    /*byte 0,1 is temperature byte 4,5 is humidity*/
    uint8_t SHT30_Data_Buffer[6];
    memset(SHT30_Data_Buffer, 0, 6);
    uint8_t send_data[2] = {0xE0, 0x00};

    uint32_t send_len = 2;
    IoTI2cWrite(I2C_HANDLE, SHT30_I2C_ADDRESS, send_data, send_len);

    uint32_t receive_len = 6;
    IoTI2cRead(I2C_HANDLE, SHT30_I2C_ADDRESS, SHT30_Data_Buffer, receive_len);

    /*check temperature*/
    data[0] = SHT30_Data_Buffer[0];
    data[1] = SHT30_Data_Buffer[1];
    data[2] = SHT30_Data_Buffer[2];
    rc = sht30_check_crc(data, 2, data[2]);
    if(!rc)
    {
        tmp = ((uint16_t)data[0] << 8) | data[1];
        dat[0] = sht30_calc_temperature(tmp);
    }
    
    /*check humidity*/
    data[0] = SHT30_Data_Buffer[3];
    data[1] = SHT30_Data_Buffer[4];
    data[2] = SHT30_Data_Buffer[5];
    rc = sht30_check_crc(data, 2, data[2]);
    if(!rc)
    {
        tmp = ((uint16_t)data[0] << 8) | data[1];
        dat[1] = sht30_calc_RH(tmp);
    }
}

/***************************************************************
* 函数名称: bh1750_read_data
* 说    明: 读取光照强度
* 参    数: dat：读取到的数据
* 返 回 值: 无
***************************************************************/
void bh1750_read_data(double *dat)
{
    uint8_t send_data[1] = {0x10};
    uint32_t send_len = 1;

    IoTI2cWrite(I2C_HANDLE, BH1750_I2C_ADDRESS, send_data, send_len); 

    uint8_t recv_data[2] = {0};
    uint32_t receive_len = 2;   

    IoTI2cRead(I2C_HANDLE, BH1750_I2C_ADDRESS, recv_data, receive_len);
    *dat = (float)(((recv_data[0] << 8) + recv_data[1]) / 1.2);
}

/***************************************************************
* 函数名称: i2c_dev_init
* 说    明: i2c设备初始化
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void i2c_dev_init(void)
{
    IoTI2cInit(I2C_HANDLE, EI2C_FRE_400K);
    sht30_init();
    bh1750_init();
}

/***************************************************************
* 函数名称: light_dev_init
* 说    明: rgb灯设备初始化
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void light_dev_init(void)
{
    IoTGpioInit(LED_R_GPIO_HANDLE);
    IoTGpioInit(LED_G_GPIO_HANDLE);
    IoTGpioInit(LED_B_GPIO_HANDLE);
    IoTGpioSetDir(LED_R_GPIO_HANDLE, IOT_GPIO_DIR_OUT);
    IoTGpioSetDir(LED_G_GPIO_HANDLE, IOT_GPIO_DIR_OUT);
    IoTGpioSetDir(LED_B_GPIO_HANDLE, IOT_GPIO_DIR_OUT);
}

/***************************************************************
* 函数名称: light_set_state
* 说    明: 控制灯状态
* 参    数: bool state true：打开 false：关闭
* 返 回 值: 无
***************************************************************/
void light_set_state(bool state)
{
    static bool last_state = false;

    if (state == last_state)
    {
        return;
    }

    if (state)
    {
        IoTGpioSetOutputVal(LED_R_GPIO_HANDLE, IOT_GPIO_VALUE1);
        IoTGpioSetOutputVal(LED_G_GPIO_HANDLE, IOT_GPIO_VALUE1);
        IoTGpioSetOutputVal(LED_B_GPIO_HANDLE, IOT_GPIO_VALUE1);
    }
    else
    {
        IoTGpioSetOutputVal(LED_R_GPIO_HANDLE, IOT_GPIO_VALUE0);
        IoTGpioSetOutputVal(LED_G_GPIO_HANDLE, IOT_GPIO_VALUE0);
        IoTGpioSetOutputVal(LED_B_GPIO_HANDLE, IOT_GPIO_VALUE0);
    }

    last_state = state;
}

/***************************************************************
* 函数名称: motor_dev_init
* 说    明: 电机初始化
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void motor_dev_init(void)
{
    IoTPwmInit(MOTOR_PWM_HANDLE);
}

/***************************************************************
* 函数名称: motor_set_pwm
* 说    明: 设置电机pwm占空比
* 参    数: unsigned int duty 占空比
* 返 回 值: 无
***************************************************************/
void motor_set_pwm(unsigned int duty)
{
    IoTPwmStart(MOTOR_PWM_HANDLE, duty, 1000);
}

/***************************************************************
* 函数名称: motor_set_state
* 说    明: 控制电机状态
* 参    数: bool state true：打开 false：关闭
* 返 回 值: 无
***************************************************************/
void motor_set_state(bool state)
{
    static bool last_state = false;

    if (state == last_state)
    {
        return;
    }

    if (state)
    {
        motor_set_pwm(20);
    }
    else
    {
        motor_set_pwm(1);
        IoTPwmStop(MOTOR_PWM_HANDLE);
    } 

    last_state = state;
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
* 说    明: 加载lcd ui
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void lcd_load_ui(void)
{
    lcd_show_chinese(96, 0, "智慧家居", LCD_RED, LCD_WHITE, 32, 0);
    lcd_draw_line(0, 33, LCD_W, 33, LCD_BLACK);
    lcd_show_chinese(5, 34, "传感器数据", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_string(125, 34, ": ", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_chinese(5, 58, "温度    ", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_chinese(5, 82, "湿度    ", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_chinese(5, 106, "光照强度", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_chinese(221, 58, "℃", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_string(221, 82, "%", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_string(221, 106, "Lux", LCD_RED, LCD_WHITE, 24, 0);
    lcd_draw_line(0, 131, LCD_W, 131, LCD_BLACK);
    lcd_show_chinese(5, 132, "设备状态", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_string(101, 132, ": ", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_chinese(5, 156, "灯光", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_string(53, 156, ": ", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_chinese(5, 180, "电机", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_string(53, 180, ": ", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_chinese(5, 204, "自动", LCD_RED, LCD_WHITE, 24, 0);
    lcd_show_string(53, 204, ": ", LCD_RED, LCD_WHITE, 24, 0);
}

/***************************************************************
* 函数名称: lcd_set_temperature
* 说    明: 设置温度显示
* 参    数: double temperature 温度
* 返 回 值: 无
***************************************************************/
void lcd_set_temperature(double temperature)
{
    uint8_t buf[50] = {0};

    sprintf(buf, ": %.2f ", temperature);

    lcd_show_string(101, 58, buf, LCD_RED, LCD_WHITE, 24, 0);
}

/***************************************************************
* 函数名称: lcd_set_humidity
* 说    明: 设置湿度显示
* 参    数: double humidity 湿度
* 返 回 值: 无
***************************************************************/
void lcd_set_humidity(double humidity)
{
    uint8_t buf[50] = {0};

    sprintf(buf, ": %.2f ", humidity);

    lcd_show_string(101, 82, buf, LCD_RED, LCD_WHITE, 24, 0);
}

/***************************************************************
* 函数名称: lcd_set_illumination
* 说    明: 设置光照强度显示
* 参    数: double illumination 光照强度
* 返 回 值: 无
***************************************************************/
void lcd_set_illumination(double illumination)
{
    uint8_t buf[50] = {0};

    sprintf(buf, ": %.2f ", illumination);

    lcd_show_string(101, 106, buf, LCD_RED, LCD_WHITE, 24, 0);
}

/***************************************************************
* 函数名称: lcd_set_light_state
* 说    明: 设置灯状态显示
* 参    数: bool state true：显示"打开" false：显示"关闭"
* 返 回 值: 无
***************************************************************/
void lcd_set_light_state(bool state)
{
    if (state)
    {
        lcd_show_chinese(77, 156, "开启", LCD_RED, LCD_WHITE, 24, 0);
    }
    else
    {
        lcd_show_chinese(77, 156, "关闭", LCD_RED, LCD_WHITE, 24, 0);
    }
}

/***************************************************************
* 函数名称: lcd_set_motor_state
* 说    明: 设置电机状态显示
* 参    数: bool state true：显示"打开" false：显示"关闭"
* 返 回 值: 无
***************************************************************/
void lcd_set_motor_state(bool state)
{
    if (state)
    {
        lcd_show_chinese(77, 180, "开启", LCD_RED, LCD_WHITE, 24, 0);
    }
    else
    {
        lcd_show_chinese(77, 180, "关闭", LCD_RED, LCD_WHITE, 24, 0);
    }
}

/***************************************************************
* 函数名称: lcd_set_auto_state
* 说    明: 设置自动模式状态显示
* 参    数: bool state true：显示"打开" false：显示"关闭"
* 返 回 值: 无
***************************************************************/
void lcd_set_auto_state(bool state)
{
    if (state)
    {
        lcd_show_chinese(77, 204, "开启", LCD_RED, LCD_WHITE, 24, 0);
    }
    else
    {
        lcd_show_chinese(77, 204, "关闭", LCD_RED, LCD_WHITE, 24, 0);
    }
}
