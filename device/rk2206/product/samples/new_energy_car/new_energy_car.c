#include <stdio.h>
#include <stdbool.h>

#include "lcd.h"
#include "iot_adc.h"
#include "iot_errno.h"
#include "iot_pwm.h"
#include "iot.h"
#include "config_network.h"
#include "MQTTClient.h"
#include "cJSON.h"

#include "ohos_init.h"
#include "los_task.h"

#define LED_R EPWMDEV_PWM1_M1
#define LED_G EPWMDEV_PWM7_M1
#define LED_B EPWMDEV_PWM0_M1

#define ROUTE_SSID "YOUR_WIFI_SSID"   // WiFi账号
#define ROUTE_PASSWORD "YOUR_PASSWORD" // WiFi密码

// #define ROUTE_SSID
// #define ROUTE_PASSWORD
#define ON 1  // 开启
#define OFF 0 // 关闭

/* 按键对应ADC通道 */
#define KEY_ADC_CHANNEL 7

int AirConditionStatus = ON; // 空调开关状态
int AirConditionT = 26;      // 默认空调开启温度
int LEDStatus = OFF;         // 车灯状态
int WindowsSatus = OFF;      // 车窗状态
int TrunKSatus = OFF;        // 后备箱状态
int CarLockSatus = ON;       // 车锁状态

void WiFi_Connect(void *args)
{
    printf("Start to connect network\n");
    uint8_t mac_address[12] = {0x00, 0xdc, 0xb6, 0x90, 0x01, 0x00, 0};

    char ssid[32] = ROUTE_SSID;
    char password[32] = ROUTE_PASSWORD;
    char mac_addr[32] = {0};

    FlashDeinit();
    FlashInit();

    VendorSet(VENDOR_ID_WIFI_MODE, "STA", 3);
    VendorSet(VENDOR_ID_MAC, mac_address, 6);
    VendorSet(VENDOR_ID_WIFI_ROUTE_SSID, ssid, sizeof(ssid));
    VendorSet(VENDOR_ID_WIFI_ROUTE_PASSWD, password, sizeof(password));

reconnect:
    SetWifiModeOff();
    int ret = SetWifiModeOn();
    if (ret != 0)
    {
        printf("wifi connect failed,please check wifi config and the AP!\n");
        return;
    }
    else
    {
        printf("wifi connect success!\n");
    }

    while (1)
    {
        if (!WaitMsg())
        {
            goto reconnect;
        }
        LOS_Msleep(1000);
    }
}

void LED()
{
    unsigned int ret;
    unsigned int duty = 99;
    unsigned int toggle = 0;

    ret = IoTPwmInit(LED_G | LED_R | LED_B);
    if (ret != 0)
    {
        printf("LED init failed!\n");
    }
    ret = IoTPwmInit(LED_R);
    if (ret != 0)
    {
        printf("LED init failed!\n");
    }
    ret = IoTPwmInit(LED_B);
    if (ret != 0)
    {
        printf("LED init failed!\n");
    }
    while (1)
    {
        if (LEDStatus)
        {
            ret = IoTPwmStart(LED_R, duty, 1000);
            if (ret != 0)
            {
                printf("IoTPwmStart failed(%d)\n", LED_R);
                continue;
            }

            ret = IoTPwmStart(LED_G, duty, 1000);
            if (ret != 0)
            {
                printf("IoTPwmStart failed(%d)\n", LED_G);
                continue;
            }

            ret = IoTPwmStart(LED_B, duty, 1000);
            if (ret != 0)
            {
                printf("IoTPwmStart failed(%d)\n", LED_B);
                continue;
            }
        }
        else
        {
            ret = IoTPwmStop(LED_R);
            if (ret != 0)
            {
                printf("IoTPwmStart failed(%d)\n", LED_R);
                continue;
            }

            ret = IoTPwmStop(LED_G);
            if (ret != 0)
            {
                printf("IoTPwmStart failed(%d)\n", LED_G);
                continue;
            }

            ret = IoTPwmStop(LED_B);
            if (ret != 0)
            {
                printf("IoTPwmStart failed(%d)\n", LED_B);
                continue;
            }
        }
    }
}

/***************************************************************
 * 函数名称: Get_Voltage
 * 说    明: 获取ADC电压值
 * 参    数: 无
 * 返 回 值: 电压值
 ***************************************************************/
static float adc_get_voltage()
{
    unsigned int ret = IOT_SUCCESS;
    unsigned int data = 0;

    /* 获取ADC值 */
    ret = IoTAdcGetVal(KEY_ADC_CHANNEL, &data);

    if (ret != IOT_SUCCESS)
    {
        printf("%s, %s, %d: ADC Read Fail\n", __FILE__, __func__, __LINE__);
        return 0.0;
    }

    return (float)(data * 3.3 / 1024.0);
}

void ChageDate()
{
    float voltage;
    unsigned int ret;

    ret = IoTAdcInit(KEY_ADC_CHANNEL);

    if (ret != IOT_SUCCESS)
    {
        printf("%s, %s, %d: ADC Init fail\n", __FILE__, __func__, __LINE__);
    }

    while (1)
    {
        /*获取电压值*/
        voltage = adc_get_voltage();

        if (voltage > 3.2)
        {
            continue;
        }
        else if (voltage > 1.50)
        {
            printf("LEFT\n");
            if (AirConditionT > 17)
            {
                AirConditionT -= 1;
            }
        }
        else if (voltage > 1.0)
        {
            printf("DOWN\n");
            // 开关空调
            AirConditionStatus = !AirConditionStatus;
        }
        else if (voltage > 0.5)
        {
            printf("RIGHT\n");
            if (AirConditionT < 31)
            {
                AirConditionT += 1;
            }
        }
        else
        {
            printf("UP\n");
            // 开关车灯
            LEDStatus = !LEDStatus;
        }

        /* 睡眠1秒 */
        LOS_Msleep(100);
    }
}

void Test()
{
    MQTT_Init();
    MessageData *data;
    while (1)
    {
        // GetMqttMsg(data);
        LOS_Msleep(1000);
    }
}

// 绘制显示界面
void LCD_UI()
{
    uint32_t ret = 0;
    ret = lcd_init();

    if (ret != 0)
    {
        printf("LCD init failed(%d)\n", ret);
    }
    printf("LCD init success!\n");

    lcd_fill(0, 0, LCD_W, LCD_H, LCD_WHITE);
    char *state;
    while (1)
    {
        // ChageAirConditionT();
        // 标题文字
        lcd_show_string(
            80, 0,
            "New Energy",
            LCD_BLUE,
            LCD_WHITE,
            32, 0);
        lcd_show_string(
            74, 35,
            "Car Control",
            LCD_BLUE,
            LCD_WHITE,
            32, 0);
        // 空调
        lcd_show_string(
            15, 70,
            "Air Condition:",
            LCD_BLACK,
            LCD_WHITE,
            24, 0);
        if (AirConditionStatus == ON)
        {
            lcd_show_int_num(
                190, 70,
                AirConditionT, 3,
                LCD_RED,
                LCD_WHITE,
                24);
            lcd_show_string(
                240, 70,
                "degree",
                LCD_RED,
                LCD_WHITE,
                24, 0);
        }
        else
        {
            lcd_show_string(
                190, 70,
                "Off       ",
                LCD_RED,
                LCD_WHITE,
                24, 0);
        }
        // 车门锁
        lcd_show_string(
            15, 100,
            "Lock Car:",
            LCD_BLACK,
            LCD_WHITE,
            24, 0);
        // if()
        lcd_show_string(
            128, 100,
            "On",
            LCD_RED,
            LCD_WHITE,
            24, 0);
        // 车窗
        lcd_show_string(
            165, 100,
            "Windows:",
            LCD_BLACK,
            LCD_WHITE,
            24, 0);
        lcd_show_string(
            265, 100,
            "On",
            LCD_RED,
            LCD_WHITE,
            24, 0);
        // 车灯
        lcd_show_string(
            15, 130,
            "Car LED:",
            LCD_BLACK,
            LCD_WHITE,
            24, 0);
        if (LEDStatus)
            state = "On ";
        else
            state = "Off";
        lcd_show_string(
            128, 130,
            state,
            LCD_RED,
            LCD_WHITE,
            24, 0);
        // 后备箱
        lcd_show_string(
            165, 130,
            "Trunk:",
            LCD_BLACK,
            LCD_WHITE,
            24, 0);
        lcd_show_string(
            255, 130,
            "On",
            LCD_RED,
            LCD_WHITE,
            24, 0);
        LOS_Msleep(10);
    }
}

void main_process()
{
    unsigned int thread_lcdui;
    TSK_INIT_PARAM_S lcd = {0};
    unsigned int ret = LOS_OK;

    lcd.pfnTaskEntry = (TSK_ENTRY_FUNC)LCD_UI;
    lcd.uwStackSize = 20480;
    lcd.pcName = "LCD Process";
    lcd.usTaskPrio = 24;
    ret = LOS_TaskCreate(&thread_lcdui, &lcd);
    if (ret != LOS_OK)
    {
        printf("Fail to create LCD task ret:0x%x\n", ret);
        return;
    }

    unsigned int thread_changeair;
    TSK_INIT_PARAM_S ca = {0};
    ret = LOS_OK;
    ca.pfnTaskEntry = (TSK_ENTRY_FUNC)ChageDate;
    ca.uwStackSize = 1024;
    ca.pcName = "Chage Data";
    ca.usTaskPrio = 24;
    ret = LOS_TaskCreate(&thread_changeair, &ca);
    if (ret != LOS_OK)
    {
        printf("Fail to create AirCondition task ret:0x%x\n", ret);
        return;
    }

    unsigned int thread_led;
    TSK_INIT_PARAM_S led = {0};
    ret = LOS_OK;
    led.pfnTaskEntry = (TSK_ENTRY_FUNC)LED;
    led.uwStackSize = 1024;
    led.pcName = "LED";
    led.usTaskPrio = 24;
    ret = LOS_TaskCreate(&thread_led, &led);
    if (ret != LOS_OK)
    {
        printf("Fail to create LED task ret:0x%x\n", ret);
        return;
    }

    unsigned int thread_wifi;
    TSK_INIT_PARAM_S wifi = {0};
    ret = LOS_OK;
    wifi.pfnTaskEntry = (TSK_ENTRY_FUNC)WiFi_Connect;
    wifi.uwStackSize = 2048;
    wifi.pcName = "Wifi Connection";
    wifi.usTaskPrio = 24;
    ret = LOS_TaskCreate(&thread_wifi, &wifi);
    if (ret != LOS_OK)
    {
        printf("Fail to WiFi task ret:0x%x\n", ret);
        return;
    }

    unsigned int thread_test;
    TSK_INIT_PARAM_S test = {0};
    ret = LOS_OK;
    test.pfnTaskEntry = (TSK_ENTRY_FUNC)Test;
    test.uwStackSize = 20480 * 5;
    test.pcName = "test";
    test.usTaskPrio = 24;
    ret = LOS_TaskCreate(&thread_test, &test);
    if (ret != LOS_OK)
    {
        printf("Fail to Test task ret:0x%x\n", ret);
        return;
    }
}

APP_FEATURE_INIT(main_process);