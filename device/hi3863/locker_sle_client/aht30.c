#include "aht30.h"      // AHT30数字温湿度传感器驱动接口
#include <stdio.h>      
#include <unistd.h>     
#include <string.h>  
#include "iot_i2c.h"
#include "iot_errno.h"


#define AHT30_I2C_IDX 1
#define AHT30_STARTUP_TIME     3//上电启动时间（30ms）
#define AHT30_CALIBRATION_TIME 4//校准时间（40ms）
#define AHT30_MEASURE_TIME     8//测量时间（75ms）
#define AHT30_DEVICE_ADDR   0x38//AHT30数字温湿度传感器的从机地址
#define AHT30_CMD_CALIBRATION       0xBE//校准命令
#define AHT30_CMD_CALIBRATION_ARG0  0x08
#define AHT30_CMD_CALIBRATION_ARG1  0x00

/**
 * 传感器在采集时需要时间,主机发出测量指令（0xAC）后，延时75毫秒以上再读取转换后的数据并判断返回的状态位是否正常。
 * 若状态比特位[Bit7]为0代表数据可正常读取，为1时传感器为忙状态，主机需要等待数据处理完成。
 **/
#define AHT30_CMD_TRIGGER       0xAC// 触发测量命令
#define AHT30_CMD_TRIGGER_ARG0  0x33
#define AHT30_CMD_TRIGGER_ARG1  0x00

/** 用于在无需关闭和再次打开电源的情况下，重新启动传感器系统，软复位所需时间不超过30 毫秒*/
#define AHT30_CMD_RESET      0xBA //软复位命令
#define AHT30_CMD_STATUS     0x71// 获取状态命令


/**
 * STATUS 命令回复：
 * 1. 初始化后触发测量之前，STATUS 只回复 1B 状态值；
 * 2. 触发测量之后，STATUS 回复6B： 1B 状态值 + 2B 湿度 + 4b湿度 + 4b温度 + 2B 温度
 *      RH = Srh / 2^30 * 100%
 *      T  = St  / 2^30 * 300 - 50
 **/
#define AHT30_STATUS_BUSY_SHIFT 7

#define AHT30_STATUS_BUSY_MASK  (0x1<<AHT30_STATUS_BUSY_SHIFT)

#define AHT30_STATUS_BUSY(status) ((status & AHT30_STATUS_BUSY_MASK) >> AHT30_STATUS_BUSY_SHIFT)

#define AHT30_STATUS_MODE_SHIFT 5

#define AHT30_STATUS_MODE_MASK  (0x3<<AHT30_STATUS_MODE_SHIFT)

#define AHT30_STATUS_MODE(status) ((status & AHT30_STATUS_MODE_MASK) >> AHT30_STATUS_MODE_SHIFT)

#define AHT30_STATUS_CALI_SHIFT 3

#define AHT30_STATUS_CALI_MASK  (0x1<<AHT30_STATUS_CALI_SHIFT)

#define AHT30_STATUS_CALI(status) ((status & AHT30_STATUS_CALI_MASK) >> AHT30_STATUS_CALI_SHIFT)

#define AHT30_STATUS_RESPONSE_MAX 6

#define AHT30_RESLUTION            (1<<20)


#define AHT30_MAX_RETRY 10


// AHT30读函数
static uint32_t AHT30_Read(uint8_t *buffer, uint32_t buffLen)
{
    uint32_t retval = IoTI2cRead(AHT30_I2C_IDX,     
                                 AHT30_DEVICE_ADDR,  
                                 buffer,          
                                 buffLen);         
    if (retval != IOT_SUCCESS)                   
    {
        printf("[AHT30] I2cRead() failed, ret=0x%0X!\n", retval);
        return retval;
    }
    return IOT_SUCCESS;
}

// AHT30写函数
static uint32_t AHT30_Write(uint8_t *buffer, uint32_t buffLen)
{
    // 向I2C设备中写入数据
    uint32_t retval = IoTI2cWrite(AHT30_I2C_IDX,    
                                  AHT30_DEVICE_ADDR, 
                                  buffer,        
                                  buffLen);         
    if (retval != IOT_SUCCESS)                   
    {
        printf("[AHT30] I2cWrite() failed, ret=0x%0X!\n", retval);
        return retval;
    }
    return IOT_SUCCESS;
}


// 发送获取状态命令
static uint32_t AHT30_StatusCommand(void)
{
    uint8_t statusCmd[] = { AHT30_CMD_STATUS };
    printf("[AHT30] Sending status command: 0x%02X\n", AHT30_CMD_STATUS);
    return AHT30_Write(statusCmd, sizeof(statusCmd));
}

// 发送软复位命令
static uint32_t AHT30_ResetCommand(void)
{
    uint8_t resetCmd[] = {AHT30_CMD_RESET};
    printf("[AHT30] Sending reset command: 0x%02X\n", AHT30_CMD_RESET);
    return AHT30_Write(resetCmd, sizeof(resetCmd));
}

// 发送初始化校准命令
static uint32_t AHT30_CalibrateCommand(void)
{
    uint8_t clibrateCmd[] = {AHT30_CMD_CALIBRATION, AHT30_CMD_CALIBRATION_ARG0, AHT30_CMD_CALIBRATION_ARG1};
    printf("[AHT30] Sending calibrate command: 0x%02X 0x%02X 0x%02X\n",
           AHT30_CMD_CALIBRATION, AHT30_CMD_CALIBRATION_ARG0, AHT30_CMD_CALIBRATION_ARG1);
    return AHT30_Write(clibrateCmd, sizeof(clibrateCmd));
}


/** 读取温湿度值之前， 首先要看状态字的校准使能位Bit[3]是否为 1(通过发送0x71可以获取一个字节的状态字)，
如果不为1，要发送0xBE命令(初始化)，此命令参数有两个字节， 第一个字节为0x08，第二个字节为0x00。**/
uint32_t AHT30_Calibrate(void)
{
    uint32_t retval = 0;
    uint8_t statusByte = 0;  // 修复：只读取1字节状态
    
    printf("[AHT30] Start calibration...\n");
    retval = AHT30_StatusCommand();
    if (retval != IOT_SUCCESS) {
        printf("[AHT30] Send status command failed!\n");
        return retval;
    }

    // 修复：只读取1字节状态
    retval = AHT30_Read(&statusByte, 1);
    if (retval != IOT_SUCCESS) {
        printf("[AHT30] Read status byte failed!\n");
        return retval;
    }
    // 打印状态字节详情
    printf("[AHT30] Read status byte: 0x%02X (busy=%d, cali=%d, mode=0x%02X)\n",
           statusByte,
           AHT30_STATUS_BUSY(statusByte),
           AHT30_STATUS_CALI(statusByte),
           AHT30_STATUS_MODE(statusByte));

    // 检查忙状态和校准状态
    if (AHT30_STATUS_BUSY(statusByte) || !AHT30_STATUS_CALI(statusByte)) {
        printf("[AHT30] Need reset and recalibrate (busy=%d, cali=%d)\n",
               AHT30_STATUS_BUSY(statusByte), AHT30_STATUS_CALI(statusByte));
        
        retval = AHT30_ResetCommand();
        if (retval != IOT_SUCCESS) {
            printf("[AHT30] Send reset command failed!\n");
            return retval;
        }
        osDelay(AHT30_STARTUP_TIME);
        printf("[AHT30] Reset done, waited %d*10ms\n", AHT30_STARTUP_TIME);
        
        retval = AHT30_CalibrateCommand();
        if (retval != IOT_SUCCESS) {
            printf("[AHT30] Send calibrate command failed!\n");
            return retval;
        }
        osDelay(AHT30_CALIBRATION_TIME);
        printf("[AHT30] Calibrate command sent, waited %d*10ms\n", AHT30_CALIBRATION_TIME);
        
        // 再次检查校准状态
        retval = AHT30_StatusCommand();
        if (retval != IOT_SUCCESS) {
            printf("[AHT30] Send status command (after cali) failed!\n");
            return retval;
        }
        
        retval = AHT30_Read(&statusByte, 1);
        if (retval != IOT_SUCCESS) {
            printf("[AHT30] Read status byte (after cali) failed!\n");
            return retval;
        }
        printf("[AHT30] Read status byte (after cali): 0x%02X (busy=%d, cali=%d)\n",
               statusByte, AHT30_STATUS_BUSY(statusByte), AHT30_STATUS_CALI(statusByte));
        
        // 确保校准成功
        if (!AHT30_STATUS_CALI(statusByte)) {
            printf("[AHT30] Calibration failed! Cali bit not set\n");
            return IOT_FAILURE;
        }
    }
    printf("[AHT30] Calibration success!\n");
    return IOT_SUCCESS;
}

//发送触发测量命令，开始测量
uint32_t AHT30_StartMeasure(void)
{
    uint8_t triggerCmd[] = {AHT30_CMD_TRIGGER, AHT30_CMD_TRIGGER_ARG0, AHT30_CMD_TRIGGER_ARG1};
    printf("[AHT30] Sending measure command: 0x%02X 0x%02X 0x%02X\n",
           AHT30_CMD_TRIGGER, AHT30_CMD_TRIGGER_ARG0, AHT30_CMD_TRIGGER_ARG1);
    return AHT30_Write(triggerCmd, sizeof(triggerCmd));
}

// 接收测量结果，拼接转换为标准值
uint32_t AHT30_GetMeasureResult(float* temp, float* humi)
{
    uint32_t retval = 0, i = 0;
    if (temp == NULL || humi == NULL) {
        printf("[AHT30] Temp/humi pointer is NULL!\n");
        return IOT_FAILURE;
    }

    uint8_t buffer[AHT30_STATUS_RESPONSE_MAX] = { 0 };
    memset(&buffer, 0x0, sizeof(buffer));

    // 读取6字节原始数据
    retval = AHT30_Read(buffer, sizeof(buffer));
    if (retval != IOT_SUCCESS) {
        printf("[AHT30] Read measure data failed!\n");
        return retval;
    }
    // 打印原始数据
    printf("[AHT30] Raw measure data: 0x%02X 0x%02X 0x%02X 0x%02X 0x%02X 0x%02X\n",
           buffer[0], buffer[1], buffer[2], buffer[3], buffer[4], buffer[5]);

    // 检查传感器是否忙
    for (i = 0; AHT30_STATUS_BUSY(buffer[0]) && i < AHT30_MAX_RETRY; i++) {
        printf("[AHT30] Sensor busy (retry=%d), wait %d*10ms\n", i+1, AHT30_MEASURE_TIME);
        osDelay(AHT30_MEASURE_TIME);
        retval = AHT30_Read(buffer, sizeof(buffer));
        if (retval != IOT_SUCCESS) {
            printf("[AHT30] Read data failed during retry %d!\n", i+1);
            return retval;
        }
        printf("[AHT30] Retry %d raw data: 0x%02X 0x%02X 0x%02X 0x%02X 0x%02X 0x%02X\n",
               i+1, buffer[0], buffer[1], buffer[2], buffer[3], buffer[4], buffer[5]);
    }

    if (i >= AHT30_MAX_RETRY) {
        printf("[AHT30] Device always busy after %d retries!\r\n", AHT30_MAX_RETRY);
        return IOT_FAILURE;
    }

    if (buffer[0] == 0xFF) {
        printf("[AHT30] Invalid data (0xFF), sensor may not be connected!\r\n");
        return IOT_FAILURE;
    }

    // 打印数据解析过程
    uint32_t humiRaw = ((uint32_t)buffer[1] << 12) | ((uint32_t)buffer[2] << 4) | ((buffer[3] & 0xF0) >> 4);
    uint32_t tempRaw = ((uint32_t)(buffer[3] & 0x0F) << 16) | ((uint32_t)buffer[4] << 8) | (uint32_t)buffer[5];
    printf("[AHT30] Parsed raw data: humiRaw=0x%08X, tempRaw=0x%08X\n", humiRaw, tempRaw);

    // 计算温湿度
    *humi = (humiRaw / (float)AHT30_RESLUTION) * 100.0f;
    *temp = (tempRaw / (float)AHT30_RESLUTION) * 200.0f - 50.0f;
    printf("[AHT30] Calculated: Temp=%.2f°C, Humi=%.2f%%\n", *temp, *humi);

    return IOT_SUCCESS;
}