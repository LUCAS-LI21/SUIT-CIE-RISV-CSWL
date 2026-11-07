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
#include <stdint.h>

#include "iot_i2c.h"
#include "iot_errno.h"

/* bh1750对应i2c */
#define BH1750_I2C_PORT EI2C0_M2

/* bh1750地址 */
#define BH1750_I2C_ADDRESS      0x23

/***************************************************************
* 函数名称: bh1750_init
* 说    明: 初始化BH1750，设置测量周期
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void bh1750_init(void)
{
    uint32_t ret = 0;
    uint8_t send_data[1] = {0x10};
    uint32_t send_len = 1;

    ret = IoTI2cInit(BH1750_I2C_PORT, EI2C_FRE_400K);
    if (ret != IOT_SUCCESS)
    {
        printf("i2c init fail!\r\v");
    }

    IoTI2cWrite(BH1750_I2C_PORT, BH1750_I2C_ADDRESS, send_data, send_len); 
}

/***************************************************************
* 函数名称: bh1750_read_data
* 说    明: 读取数据
* 参    数: dat：读取到的数据
* 返 回 值: 无
***************************************************************/
void bh1750_read_data(double *dat)
{
    uint8_t recv_data[2] = {0};
    uint32_t receive_len = 2;   

    IoTI2cRead(BH1750_I2C_PORT, BH1750_I2C_ADDRESS, recv_data, receive_len);
    *dat = (float)(((recv_data[0] << 8) + recv_data[1]) / 1.2);
}
