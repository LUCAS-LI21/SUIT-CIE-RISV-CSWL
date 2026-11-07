#include "iot_gpio.h"
#include "wifi_store.h"

#define GPIO_KEY_RESET        GPIO0_PC7
#define PRESSED     1
#define NO_PRESSED  0

void key_process()
{
    unsigned int ret;

    /* 初始化引脚为GPIO */
    IoTGpioInit(GPIO_KEY_RESET);
    /* 引脚配置为输入 */
    IoTGpioSetDir(GPIO_KEY_RESET, IOT_GPIO_DIR_IN);

    int is_pressed = NO_PRESSED;
    int press_count = 0;
    while (1)
    {
        IotGpioValue val;
        IoTGpioGetInputVal(GPIO_KEY_RESET, &val);
        if((val == 0) &&(is_pressed == NO_PRESSED)) {

            //消除抖动
            LOS_Msleep(10);
            IoTGpioGetInputVal(GPIO_KEY_RESET, &val);
            if(val == 0){
                is_pressed = PRESSED;
                printf("pressed\n");
              
            }
        }else if((val == 1) &&(is_pressed == PRESSED)){
                is_pressed = NO_PRESSED;
                printf("no pressed\n");
                int press_count = 0;
        }else if((val == 0) &&(is_pressed == PRESSED)){//长按
            press_count ++;
            /*每次50ms,100次=5s */
            if(press_count>=100){
                printf("start to reboot");
                /*重启前清除配网信息,和其他需要清除的内容*/
                clearWifiInfo();
                    //重启设备
                RebootDevice(3);

            }

        }
        
        /* 睡眠1秒 */
        LOS_Msleep(50);
    }
}