# 星闪套件PRO版OpenHarmonyOS外设开发-**智慧语音场景**模块
## 实验内容
本例程基于**SU-03T语音模块**与星闪套件PRO版的UART2通信，实现完整的语音交互场景：通过“小智小智”唤醒语音模块后，下发“开灯/打开灯光”“关灯/关闭灯光”“警报/打开警报”“关闭警报”“当前温度”“当前湿度”等指令，开发板解析UART指令帧后控制LED/蜂鸣器硬件动作，并回传指令执行结果至SU-03T（如“灯光已开启”）；同时AHT30温湿度传感器采集的数据会通过语音播报反馈，实现**唤醒-指令下发-硬件执行-语音反馈**的全链路交互。

![Smart_Voice原理图](./figures/f4_1.jpg)

![Smart_Voice原理图](./figures/f4_2.jpg)

## 程序设计
本例程以**SU-03T语音模块的指令交互逻辑**为核心，采用双线程架构实现功能解耦：
1. **`uart2_task_thread`核心线程**：负责UART2与SU-03T的通信配置、LED/蜂鸣器硬件初始化、指令解析与硬件控制，是语音交互的核心载体；
2. **`aht30_collect_thread`辅助线程**：负责I2C总线与AHT30传感器初始化，持续采集温湿度数据并更新全局变量，为“当前温度/湿度”指令提供数据来源。

### 核心交互流程（SU-03T+开发板）
1. **唤醒环节**：用户说“小智小智”，SU-03T回复“我在”“你说”“有什么可以帮到你”，进入指令接收状态；
2. **指令下发**：用户说指令（如“开灯”），SU-03T将指令转换为6字节UART帧发送至开发板；
3. **指令解析**：开发板UART2接收中断回调匹配指令帧，更新硬件控制标志；
4. **硬件执行**：开发板循环检测控制标志，执行LED亮/灭、蜂鸣器启停动作；
5. **语音反馈**：开发板回传确认帧至SU-03T，SU-03T语音播报执行结果（如“灯光已开启”）；
6. **温湿度查询**：用户说“当前温度/湿度”，开发板回传温湿度数据帧，SU-03T播报具体数值（如“当前温度为25.5摄氏度”）。


### 核心设计：SU-03T指令-动作-语音反馈映射表
针对SU-03T的指令与语音反馈逻辑，定义`CmdCtrlMap`结构体，绑定“SU-03T指令帧-开发板动作-回传确认帧”的对应关系：
```c
typedef struct {
    uint8_t cmd_frame[CMD_FRAME_LEN];    // SU-03T下发的6字节指令帧
    uint8_t resp_frame[RESP_FRAME_LEN];  // 开发板回传的5字节确认帧
    volatile bool *ctrl_flag;            // 硬件控制标志（LED/蜂鸣器）
    bool target_flag_val;                // 控制标志目标值（ON/OFF）
    volatile bool *update_flag;          // 硬件更新触发标志
    const char *su_voice_resp;           // SU-03T对应的语音反馈内容
} CmdCtrlMap;
```

**SU-03T指令与开发板动作的映射关系**（匹配SU-03T的指令词与语音反馈）：
| 用户指令词       | SU-03T指令帧（6字节） | 开发板回传帧（5字节） | 硬件动作               | SU-03T语音反馈   |
|------------------|-----------------------|-----------------------|------------------------|------------------|
| 开灯/打开灯光    | AA 55 01 00 55 AA     | AA 55 00 55 AA        | LED点亮                | 灯光已开启       |
| 关灯/关闭灯光    | AA 55 01 01 55 AA     | AA 55 01 55 AA        | LED熄灭                | 灯光已关闭       |
| 警报/打开警报    | AA 55 01 02 55 AA     | AA 55 02 55 AA        | 蜂鸣器响（50%占空比）  | 警报已开启       |
| 关闭警报         | AA 55 01 03 55 AA     | AA 55 03 55 AA        | 蜂鸣器停（0%占空比）   | 警报已关闭       |
| 当前温度         | AA 55 01 04 55 AA     | 13字节温度数据帧      | 无硬件动作             | 当前温度为X.X摄氏度 |
| 当前湿度         | AA 55 01 05 55 AA     | 13字节湿度数据帧      | 无硬件动作             | 当前湿度为X.X%   |


### API 分析
**核心头文件**
```c
#include <stdio.h>
#include <unistd.h>
#include "iot_gpio.h"
#include "iot_gpio_ex.h"
#include "iot_errno.h"
#include "ohos_init.h"
#include "cmsis_os2.h"
#include "uart.h"
#include "iot_uart.h"
#include "aht30.h"
#include "iot_i2c.h"
#include <stdarg.h>
#include <string.h>
#include <stdbool.h>
```

**核心API功能（聚焦SU-03T交互）**
| API名称                                                      | 说明                          |
| ------------------------------------------------------------ | ----------------------------- |
| int IoTUartInit(unsigned int id, const IotUartAttribute *attr); | 初始化UART2（匹配SU-03T的115200波特率） |
| errcode_t uapi_uart_register_rx_callback(...)                | 注册UART接收中断，实时接收SU-03T指令帧 |
| int IoTUartWrite(...)                                        | 回传确认帧/温湿度数据帧至SU-03T |
| unsigned int IoTGpioSetOutputVal(...)                        | 控制LED电平（响应“开灯/关灯”指令） |
| int IoTPwmStart(...)                                         | 控制蜂鸣器PWM占空比（响应“警报”指令） |


## 软件设计
**核心代码分析：SU-03T指令解析与语音反馈**
UART2接收中断回调函数匹配SU-03T的6字节指令帧，触发硬件动作并回传数据帧（驱动SU-03T语音反馈）：
```c
static void uart2_rx_callback(const void *buffer, uint16_t length, bool error) {
    unused(error);
    // 仅处理SU-03T下发的6字节指令帧
    if (g_is_rx_processing || buffer == NULL || length != CMD_FRAME_LEN) {
        return;
    }

    g_is_rx_processing = true;
    memcpy(g_recv_buf, buffer, length);

    bool cmd_matched = false;
    // 匹配LED/蜂鸣器控制指令（驱动SU-03T语音反馈）
    for (int i = 0; i < MAP_COUNT; i++) {
        if (memcmp(g_recv_buf, g_cmd_ctrl_map[i].cmd_frame, CMD_FRAME_LEN) == 0) {
            // 回传确认帧，触发SU-03T语音播报（如“灯光已开启”）
            IoTUartWrite(UART2_ID, g_cmd_ctrl_map[i].resp_frame, sizeof(g_cmd_ctrl_map[i].resp_frame));
            // 更新硬件控制标志
            osKernelLock();
            *(g_cmd_ctrl_map[i].ctrl_flag) = g_cmd_ctrl_map[i].target_flag_val;
            *(g_cmd_ctrl_map[i].update_flag) = true;
            osKernelUnlock();
            cmd_matched = true;
            break;
        }
    }

    // 匹配“当前温度”指令，回传13字节数据帧（SU-03T播报温度值）
    if (!cmd_matched && memcmp(g_recv_buf, (uint8_t[]){0xAA, 0x55, 0x01, 0x04, 0x55, 0xAA}, CMD_FRAME_LEN) == 0) {
        send_temperature_resp(); // 封装double类型温度为13字节帧
        cmd_matched = true;
    }

    // 匹配“当前湿度”指令，回传13字节数据帧（SU-03T播报湿度值）
    if (!cmd_matched && memcmp(g_recv_buf, (uint8_t[]){0xAA, 0x55, 0x01, 0x05, 0x55, 0xAA}, CMD_FRAME_LEN) == 0) {
        send_humidity_resp(); // 封装double类型湿度为13字节帧
        cmd_matched = true;
    }

    memset(g_recv_buf, 0, RECV_BUF_SIZE);
    g_is_rx_processing = false;
}
```


**核心代码分析：硬件状态更新（响应SU-03T指令）**
循环检测控制标志，将SU-03T指令转换为LED/蜂鸣器动作：
```c
static void update_hardware_state(void) {
    bool light_updated = false;
    bool alert_updated = false;
    bool light_state = false;
    bool alert_state = false;

    // 原子操作读取控制标志（避免多线程冲突）
    osKernelLock();
    light_state = g_light_ctrl_flag;
    light_updated = g_light_flag_updated;
    if (light_updated) g_light_flag_updated = false;
    
    alert_state = g_alert_ctrl_flag;
    alert_updated = g_alert_flag_updated;
    if (alert_updated) g_alert_flag_updated = false;
    osKernelUnlock();

    // 执行“开灯/关灯”动作
    if (light_updated) {
        IoTGpioSetOutputVal(LED_GPIO, light_state ? 1 : 0);
        printf("[SU-03T] 响应指令：%s → LED %s\n", 
               light_state ? "开灯" : "关灯", 
               light_state ? "ON" : "OFF");
    }
    // 执行“警报/关闭警报”动作
    if (alert_updated) {
        IoTPwmStart(PWM_PORT, alert_state ? 50 : 0, FREQUENCY);
        printf("[SU-03T] 响应指令：%s → 蜂鸣器 %s\n", 
               alert_state ? "警报" : "关闭警报", 
               alert_state ? "ON" : "OFF");
    }
}
```


## 编译调试
1. 将SU-03T语音交互代码目录（如`f4_smart_voice`）复制到OpenHarmony源码的`applications\sample\wifi-iot\app`目录下。
2. 修改`applications\sample\wifi-iot\app\BUILD.gn`，在`features`中添加模块：
```
features = [
        ...
        "f4_smart_voice:Smart_Voice_example",
        ...
    ]
```
3. 在`device\soc\hisilicon\ws63v100\sdk\build\config\target_config\ws63\config.py`的`'ram_component'`中添加：
```
"Smart_Voice_example"
```
4. 在`device\soc\hisilicon\ws63v100\sdk\libs_url\ws63\cmake\ohos.cmake`的`set(COMPONENT_LIST`中添加：
```
"Smart_Voice_example"
```
5. 根目录执行编译：
```bash
rm -rf out && hb set -p nearlink_dk_3863 && hb build -f
```


### 运行结果
代码烧录后，开发板与SU-03T完成交互，实现以下场景：
1. **唤醒交互**：
   - 用户：“小智小智”
   - SU-03T：“我在”/“你说”/“有什么可以帮到你”

2. **灯光控制**：
   - 用户：“开灯”/“打开灯光”
   - 开发板：LED点亮，串口打印`[SU-03T] 响应指令：开灯 → LED ON`
   - SU-03T：“灯光已开启”
   - 用户：“关灯”/“关闭灯光”
   - 开发板：LED熄灭，串口打印`[SU-03T] 响应指令：关灯 → LED OFF`
   - SU-03T：“灯光已关闭”

3. **警报控制**：
   - 用户：“警报”/“打开警报”
   - 开发板：蜂鸣器响起，串口打印`[SU-03T] 响应指令：警报 → 蜂鸣器 ON`
   - SU-03T：“警报已开启”
   - 用户：“关闭警报”
   - 开发板：蜂鸣器停止，串口打印`[SU-03T] 响应指令：关闭警报 → 蜂鸣器 OFF`
   - SU-03T：“警报已关闭”

4. **温湿度查询**：
   - 用户：“当前温度”
   - 开发板：回传温度数据帧，串口打印`[SU-03T] 响应指令：当前温度 → 25.5℃`
   - SU-03T：“当前温度为25.5摄氏度”
   - 用户：“当前湿度”
   - 开发板：回传湿度数据帧，串口打印`[SU-03T] 响应指令：当前湿度 → 45.2%`
   - SU-03T：“当前湿度为百分之45.2”