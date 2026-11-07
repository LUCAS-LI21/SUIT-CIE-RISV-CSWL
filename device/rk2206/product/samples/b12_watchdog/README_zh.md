# 通晓开发板基础外设开发——Watchdog

本示例将演示如何在通晓开发板上使用Watchdog。

![通晓开发板](/vendor/isoftstone/rk2206/docs/figures/tx_smart_r-rk2206.jpg)

## 程序设计

[Watchdog接口文档](/device/rockchip/hardware/docs/WatchDog.md)

### 软件设计

#### Watchdog初始化

这部分代码为watchdog初始化的代码。调用 `IoTWatchDogEnable()` 函数将芯片中的看门狗使能，并配置为20秒。

```c
void watchdog_process()
{
    IoTWatchDogEnable(20);
	......
}
```

#### Watchdog喂狗处理

调用`IoTWatchDogKick()`负责喂狗，代码如下所示：

```c
void watchdog_process()
{
    ......
    while (1)
    {
        printf("Wathdog: current(%d)\n", ++current);
        if (current <= 10)
        {
            printf("    freedog\n");
            IoTWatchDogKick();
        }
        else
        {
            printf("    not freedog\n");
        }
        
        LOS_Msleep(1000);
    }
}
```

## 编译调试

### 修改 BUILD.gn 文件

修改 `vendor/isoftstone/rk2206/sample` 路径下 BUILD.gn 文件，指定 `watchdog_example` 参与编译。

```r
"./b12_watchdog:watchdog_example",
```

修改 `device/rockchip/rk2206/sdk_liteos` 路径下 Makefile 文件，添加 `-lwatchdog_example` 参与编译。

```r
app_LIBS = -lwatchdog_example
```

### 运行结果

示例代码编译烧录代码后，按下开发板的RESET按键，通过串口助手查看日志，显示如下：

```r
watchdog_process: start
Wathdog: current(1)
    freedog
Wathdog: current(2)
    freedog
Wathdog: current(3)
    freedog
Wathdog: current(4)
    freedog
Wathdog: current(5)
    freedog
Wathdog: current(6)
    freedog
Wathdog: current(7)
    freedog
Wathdog: current(8)
    freedog
Wathdog: current(9)
    freedog
Wathdog: current(10)
    freedog
Wathdog: current(11)
    not freedog
Wathdog: current(12)
    not freedog
Wathdog: current(13)
    not freedog
Wathdog: current(14)
    not freedog
Wathdog: current(15)
    not freedog
Wathdog: current(16)
    not freedog
Wathdog: current(17)
    not freedog
Wathdog: current(18)
    not freedog
Wathdog: current(19)
    not freedog
Wathdog: current(20)
    not freedog
Wathdog: current(21)
    not freedog
Wathdog: current(22)
    not freedog
Wathdog: current(23)
    not freedog
Wathdog: current(24)
    not freedog
Wathdog: current(25)
    not freedog
Wathdog: current(26)
    not freedog
Wathdog: current(27)
    not freedog
Wathdog: current(28)
    not freedog
Wathdog: current(29)
    not freedog
Wathdog: current(30)
    not freedog
Wathdog: current(31)
    not freedog
Wathdog: current(32)
    not freedog
entering kernel init...
hilog will init.
[IOT:D]IotInit: start ....
[MAIN:D]Main: LOS_Start ...
Entering scheduler
[IOT:D]IotProcess: start ....
......
```

