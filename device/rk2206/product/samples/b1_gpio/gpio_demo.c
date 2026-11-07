#include "ohos_init.h"
#include "iot_gpio.h"

#define GPIO_TEST     GPIO0_PA5

void gpio_demo(){

    printf("gpio_demo start\n");
    //初始化IO口
    IoTGpioInit(GPIO_TEST);
    //设置GPIO为输出方向
    IoTGpioSetDir(GPIO_TEST, IOT_GPIO_DIR_OUT);
    //设置GPIO输出电平
    IoTGpioSetOutputVal(GPIO_TEST, IOT_GPIO_VALUE1);

    

}


APP_FEATURE_INIT(gpio_demo);