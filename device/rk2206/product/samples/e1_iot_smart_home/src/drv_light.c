#include "drv_light.h"
#include "iot_gpio.h"

#define LED_R_GPIO_HANDLE GPIO0_PB5
#define LED_G_GPIO_HANDLE GPIO0_PB4
#define LED_B_GPIO_HANDLE GPIO1_PD0

static bool g_light_state = false;


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

    if (state == g_light_state)
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
    g_light_state = state;

}



int get_light_state(void)
{
    return g_light_state;
}
