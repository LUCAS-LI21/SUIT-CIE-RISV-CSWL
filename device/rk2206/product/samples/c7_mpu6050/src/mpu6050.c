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
#include "mpu6050.h"

#include <stdio.h>
#include <stdint.h>

#include "los_task.h"
#include "ohos_init.h"

#include "iot_i2c.h"
#include "iot_errno.h"

#define MPU6050_I2C_PORT EI2C0_M2

#define MPU6050_I2C_ADDRESS 0x68

/***************************************************************
 * 函数名称: MPU6050_Read_Buffer
 * 说    明: I2C读取一段寄存器内容存放到指定的缓冲区
 * 参    数:  reg：目标寄存器
 *          p_buffer：缓冲区
 *          value：值
 * 返 回 值: 操作结果
 ***************************************************************/
static uint8_t MPU6050_Read_Buffer(uint8_t reg, uint8_t *p_buffer, uint16_t length)
{

    uint32_t status = 0;
    uint8_t buffer[1] = {reg};

    status = IoTI2cWrite(MPU6050_I2C_PORT, MPU6050_SLAVE_ADDRESS, buffer, 1);
    if (status != IOT_SUCCESS)
    {
        printf("Error: I2C write status:%d\n", status);
        return status;
    }

    IoTI2cRead(MPU6050_I2C_PORT, MPU6050_SLAVE_ADDRESS, p_buffer, length);
    return IOT_SUCCESS;
}

/***************************************************************
 * 函数名称: mpu6050_write_reg
 * 说    明: 写数据到MPU6050寄存器
 * 参    数:  reg：目标寄存器
 *          data
 * 返 回 值: 无
 ***************************************************************/
static void mpu6050_write_reg(uint8_t reg, uint8_t data)
{
    uint8_t send_data[2] = {reg, data};

    IoTI2cWrite(MPU6050_I2C_PORT, MPU6050_SLAVE_ADDRESS, send_data, 2);
}

/***************************************************************
 * 函数名称: mpu6050_read_register
 * 说    明: 从MPU6050寄存器读取数据
 * 参    数:  reg：目标寄存器
 *          buf：缓冲区
 *          length：长度
 * 返 回 值: 无
 ***************************************************************/
static void mpu6050_read_register(uint8_t reg, unsigned char *buf, uint8_t length)
{
    MPU6050_Read_Buffer(reg, buf, length);
}

/***************************************************************
 * 函数名称: mpu6050_read_acc
 * 说    明: 读取MPU6050的加速度数据
 * 参    数:  acc_data：加速度数据
 * 返 回 值: 无
 ***************************************************************/
static void mpu6050_read_acc(short *acc_data)
{
    uint8_t buf[6];
    mpu6050_read_register(MPU6050_ACC_OUT, buf, 6);
    acc_data[0] = (buf[0] << 8) | buf[1];
    acc_data[1] = (buf[2] << 8) | buf[3];
    acc_data[2] = (buf[4] << 8) | buf[5];
}

/***************************************************************
 * 函数名称: action_interrupt
 * 说    明: 运动中断设置
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
static void action_interrupt()
{
    mpu6050_write_reg(MPU6050_RA_MOT_THR, 0x03); // 运动阈值
    mpu6050_write_reg(MPU6050_RA_MOT_DUR, 0x14); // 检测时间20ms 单位1ms
}

/***************************************************************
 * 函数名称: mpu6050_init
 * 说    明: mpu6050初始化
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void mpu6050_init(void)
{

    IoTI2cInit(MPU6050_I2C_PORT, EI2C_FRE_100K);

    /*在初始化之前要延时一段时间，若没有延时，则断电后再上电数据可能会出错*/
    LOS_Msleep(1000);

    mpu6050_write_reg(MPU6050_RA_PWR_MGMT_1, 0X80); // 复位MPU6050
    LOS_Msleep(200);
    mpu6050_write_reg(MPU6050_RA_PWR_MGMT_1, 0X00);   // 唤醒MPU6050
    mpu6050_write_reg(MPU6050_RA_INT_ENABLE, 0X00);   // 关闭所有中断
    mpu6050_write_reg(MPU6050_RA_USER_CTRL, 0X00);    // I2C主模式关闭
    mpu6050_write_reg(MPU6050_RA_FIFO_EN, 0X00);      // 关闭FIFO
    mpu6050_write_reg(MPU6050_RA_INT_PIN_CFG, 0X80);  // 中断的逻辑电平模式,设置为0，中断信号为高电；设置为1，中断信号为低电平时。
    action_interrupt();                               // 运动中断
    mpu6050_write_reg(MPU6050_RA_CONFIG, 0x04);       // 配置外部引脚采样和DLPF数字低通滤波器
    mpu6050_write_reg(MPU6050_RA_ACCEL_CONFIG, 0x1C); // 加速度传感器量程和高通滤波器配置
    mpu6050_write_reg(MPU6050_RA_INT_PIN_CFG, 0X1C);  // INT引脚低电平平时
    mpu6050_write_reg(MPU6050_RA_INT_ENABLE, 0x40);   // 中断使能寄存器
}

/***************************************************************
 * 函数功能: 读取MPU6050的ID
 * 输入参数: 无
 * 返 回 值: 无
 * 说    明: 无
 ***************************************************************/
static uint8_t mpu6050_read_id()
{
    unsigned char buff = 0;
    mpu6050_read_register(MPU6050_RA_WHO_AM_I, &buff, 1);
    if (buff != 0x68)
    {
        printf("MPU6050 dectected error Re:%u\n", buff);
        return 0;
    }
    else
    {
        return 1;
    }
}

/***************************************************************
 * 函数名称: mpu6050_read_data
 * 说    明: 读取数据
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void mpu6050_read_data(short *dat)
{
    short accel[3];
    short temp;
    if (mpu6050_read_id() == 0)
    {
        while (1)
            ;
    }
    mpu6050_read_acc(accel);
    dat[0] = accel[0];
    dat[1] = accel[1];
    dat[2] = accel[2];
    LOS_Msleep(500);
}
