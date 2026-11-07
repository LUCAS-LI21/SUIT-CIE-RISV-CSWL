#include "adc.h"
#include "adc_porting.h"
#include "cmsis_os2.h"
#include "common_def.h"
#include "ohos_init.h"
#include "soc_osal.h"
#include "stdio.h"
#include <stdio.h>

#define ADC_CHANNEL 2 // 要读取的 ADC 通道

// K1/K2/K3 语音触发标志，由 smart_voice_example.c 定义
extern volatile bool g_k1_triggered;
extern volatile bool g_k2_triggered;
extern volatile bool g_k3_triggered;

typedef enum {
    KEY_NONE, KEY_K1, KEY_K2, KEY_K3, KEY_DOWN, KEY_UP, KEY_MID
} KeyId;

static KeyId get_key_id(uint16_t mv)
{
    if (mv <= 200)                      return KEY_K1;
    if (mv >= 400  && mv <= 700)        return KEY_K2;
    if (mv >= 850  && mv <= 1150)       return KEY_K3;
    if (mv >= 1500 && mv <= 1800)       return KEY_DOWN;
    if (mv >= 2000 && mv <= 2300)       return KEY_UP;
    if (mv >= 2500 && mv <= 2900)       return KEY_MID;
    return KEY_NONE;
}

/***************************************************************
 * 函数名称: adc_init
 * 说    明: ADC 初始化函数
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
int adc_init(void)
{
    printf("[ADC] Initializing ADC...\r\n");
    int ret = uapi_adc_init(ADC_CLOCK_NONE);
    if (ret != 0)
    {
        printf("[ADC] Failed to initialize ADC! Error code: %d\r\n", ret);
    }
    else
    {
        printf("[ADC] ADC initialized successfully.\r\n");
    }
    return ret;
}
/***************************************************************
 * 函数名称: adc_read_channel
 * 说    明: ADC 读取按键函数
 * 参    数: uint16_t *voltage_mV - 存储读取到的电压值（单位：毫伏）
 * 返 回 值: 无
 ***************************************************************/
int adc_read_channel(uint16_t *voltage_mV)
{
    int ret = adc_port_read(ADC_CHANNEL, voltage_mV);
    if (ret == 0)
    {
        printf("[ADC] Channel %d: %u mV\r\n", ADC_CHANNEL, *voltage_mV);
    }
    else
    {
        printf("[ADC] Read failed on channel %d, error code: %d\r\n", ADC_CHANNEL, ret);
    }

    if (*voltage_mV >= 2500 && *voltage_mV <= 2900)
    {
        printf("Mid Key!\r\n");
    }
    else if (*voltage_mV >= 2000 && *voltage_mV <= 2300)
    {
        printf("UP Key!\r\n");
    }
    else if (*voltage_mV >= 1500 && *voltage_mV <= 1800)
    {
        printf("Down Key!\r\n");
    }
    else if (*voltage_mV >= 0 && *voltage_mV <= 200)
    {
        printf("K1 Key!\r\n");
    }
    else if (*voltage_mV >= 400 && *voltage_mV <= 700)
    {
        printf("K2 Key!\r\n");
    }
    else if (*voltage_mV >= 850 && *voltage_mV <= 1150)
    {
        printf("K3 Key!\r\n");
    }
}
/***************************************************************
 * 函数名称: adc_key_example
 * 说    明: adc按键任务函数
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
void adc_key_main(void *arg)
{
    (void)arg;
    uint16_t voltage = 0;
    KeyId last_key = KEY_NONE;

    adc_init();
    for (;;)
    {
        adc_read_channel(&voltage);
        KeyId current_key = get_key_id(voltage);

        // 上升沿检测：按键从"无/其他"变为 K1 或 K2 时触发一次
        if (current_key != last_key) {
            if (current_key == KEY_K1) {
                printf("[KEY] K1 pressed -> 主人我饿了\r\n");
                g_k1_triggered = true;
            } else if (current_key == KEY_K2) {
                printf("[KEY] K2 pressed -> 主人我渴了\r\n");
                g_k2_triggered = true;
            } else if (current_key == KEY_K3) {
                printf("[KEY] K3 pressed -> 主人我想出去玩\r\n");
                g_k3_triggered = true;
            }
        }
        last_key = current_key;

        osDelay(50);  // 50ms 消抖
    }
}
/***************************************************************
 * 函数名称: adc_key_example
 * 说    明: 系统注册函数
 * 参    数: 无
 * 返 回 值: 无
 ***************************************************************/
static void adc_key_example(void)
{
    osThreadAttr_t attr = {0};
    attr.name = "adc_key_main";
    attr.stack_size = 4096;
    attr.priority = osPriorityNormal;

    if (osThreadNew(adc_key_main, NULL, &attr) == NULL)
    {
        printf("[ADCKeyTest] Failed to create adc_key_main thread!\n");
    }
}

APP_FEATURE_INIT(adc_key_example);