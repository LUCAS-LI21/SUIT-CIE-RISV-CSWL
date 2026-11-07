# locker_sle_server - SLE 服务端 + 按键控制

## 📋 项目概述

本项目是 OpenHarmony WS63V100 平台上的 SLE（Star Light）服务端应用，支持通过 **GPIO11 按键**触发数据发送到 SLE 客户端。

## ✨ 新增功能：按键控制

### 硬件配置
- **按键引脚**: GPIO11
- **工作模式**: 输入模式 + 上拉电阻
- **触发方式**: 按下时接地（低电平有效）
- **消抖时间**: 20ms（软件消抖）

### 电路连接
```
GPIO11 ----[按键]---- GND
         |
        [内部上拉]
         |
        VCC (3.3V)
```

**工作原理**：
- **未按下**：GPIO11 通过上拉电阻连接到 VCC，读取到高电平（1）
- **按下**：GPIO11 直接接地，读取到低电平（0）

## 🏗️ 架构设计

### 模块化结构
```
locker_sle_server/
├── BUILD.gn                    # 构建配置（已添加 libiot_gpio 依赖）
├── sle_server_app.c            # 应用入口（集成按键模块）
├── sle_server_lib.c/h          # SLE 核心功能库
├── key_module.c                # 按键模块实现（新增）
├── key_module.h                # 按键模块接口（新增）
└── README_zh.md                # 本文档
```

### 任务模型
| 任务名称 | 优先级 | 栈大小 | 功能描述 |
|---------|--------|--------|----------|
| `sle_rx_server` | Normal | 8KB | 接收 SLE 客户端数据 |
| `key_scan_task` | Normal | 4KB | 扫描按键状态并消抖 |

### 数据流向
```
用户按下 GPIO11 按键
    ↓
KeyScanTask 检测到电平变化
    ↓
软件消抖（20ms）
    ↓
OnKeyStateChanged 回调触发
    ↓
调用 sle_server_send_report_by_handle()
    ↓
通过 SLE 发送数据到客户端
```

## 🔧 编译和烧录

### 1. 编译项目
```bash
cd /home/openharmony/isoh_nl63pro
hb set -p nearlink_txnl63
hb build -f
```

### 2. 烧录固件
使用 HiBurn 或其他烧录工具将生成的固件烧录到 WS63V100 开发板。

### 3. 查看日志
打开串口调试助手，波特率设置为 **115200**，观察启动日志。

## 📊 预期日志输出

### 启动阶段
```
[KEY] Initializing key module on GPIO11...
[KEY] ✅ GPIO11 initialized
[KEY] ✅ GPIO11 set as input
[KEY] ✅ GPIO11 pull-up enabled
[KEY] Initial key state: RELEASED
[KEY] Key module initialized successfully!
[KEY] Callback registered successfully
[SLE Server] ✅ Key scan task created
[SLE Server] Server initialized, waiting for K1 key press (GPIO11) to send data...
[KEY] Key scan task started
```

### 按键按下时
```
[KEY] State changed: PRESSED
[APP] 🎯 Key pressed! Sending SLE data...
[APP] ✅ Data sent via SLE
[KEY] State changed: RELEASED
[APP] Key released
```

## 🛠️ 配置说明

### 修改按键引脚
如果需要更改按键引脚，编辑 [`key_module.h`](file:///home/openharmony/isoh_nl63pro/applications/sample/wifi-iot/app/locker_sle_server/key_module.h)：

```c
#define GPIO_KEY_NUM        11          // 改为其他GPIO编号
#define KEY_DEBOUNCE_MS     20          // 调整消抖时间（10-50ms）
#define KEY_POLL_INTERVAL   10          // 调整轮询间隔（5-20ms）
```

### 修改按键触发动作
编辑 [`sle_server_app.c`](file:///home/openharmony/isoh_nl63pro/applications/sample/wifi-iot/app/locker_sle_server/sle_server_app.c) 中的 `OnKeyStateChanged` 函数：

```c
void OnKeyStateChanged(KeyState state)
{
    if (state == KEY_STATE_PRESSED) {
        // 在这里添加自定义逻辑
        printf("Custom action on key press!\n");
        
        // 例如：发送不同数据
        uint8_t custom_data[] = "Custom message";
        sle_server_send_report_by_handle(custom_data, sizeof(custom_data));
    }
}
```

## 🔍 故障排查

### 问题1：按键无响应
**可能原因**：
1. GPIO11 未正确连接按键
2. 按键损坏或接线错误
3. 上拉电阻未生效

**解决方法**：
```bash
# 检查串口日志中是否有以下信息
[KEY] ✅ GPIO11 initialized
[KEY] ✅ GPIO11 set as input
[KEY] ✅ GPIO11 pull-up enabled
```

**硬件检查**：
- 用万用表测量 GPIO11 对地电压
  - 未按下时应为 3.3V
  - 按下时应为 0V

### 问题2：按键抖动严重
**现象**：单次按下触发多次事件

**解决方法**：
增加消抖时间：
```c
#define KEY_DEBOUNCE_MS     50  // 从20ms增加到50ms
```

### 问题3：编译错误 `undefined reference to IoTGpioInit`
**原因**：缺少 IoT GPIO 库依赖

**解决方法**：
确认 [`BUILD.gn`](file:///home/openharmony/isoh_nl63pro/applications/sample/wifi-iot/app/locker_sle_server/BUILD.gn) 中包含：
```gn
deps = [
  "//base/iothardware/peripheral/interfaces/inner_api:libiot_gpio",
]
```

## 💡 扩展功能建议

### 1. 长按检测
```c
static uint32_t press_start_time = 0;

if (state == KEY_STATE_PRESSED) {
    press_start_time = osKernelGetTickCount();
} else if (state == KEY_STATE_RELEASED) {
    uint32_t duration = osKernelGetTickCount() - press_start_time;
    if (duration > 1000) {
        printf("Long press detected (%d ms)\n", duration);
    }
}
```

### 2. 双击检测
记录两次按下的时间间隔，判断是否为双击。

### 3. 中断方式替代轮询
使用 `IoTGpioRegisterIsrFunc()` 注册中断回调，降低 CPU 占用。

## 📚 相关文档

- [SLE 服务端开发指南](README_zh.md)
- [OpenHarmony IoT HAL API 文档](https://gitee.com/openharmony/docs)
- [WS63V100 引脚映射表](device/board/isoh/nl63pro/datasheet/)

## ⚠️ 注意事项

1. **GPIO 编号确认**：确保 GPIO11 在 WS63V100 上是可用的普通 GPIO，而非特殊功能引脚
2. **电流限制**：单个 GPIO 引脚最大电流不超过 20mA
3. **电压等级**：WS63V100 GPIO 为 3.3V 电平，勿接入 5V 信号
4. **消抖必要性**：机械按键必然存在抖动，软件消抖不可省略

## 🎉 总结

本项目成功集成了：
- ✅ SLE 服务端通信功能
- ✅ GPIO11 按键输入
- ✅ 软件消抖算法（20ms）
- ✅ 按键事件回调机制
- ✅ 按键触发 SLE 数据发送

现在您可以通过按下连接到 GPIO11 的按键来触发数据发送到 SLE 客户端了！
