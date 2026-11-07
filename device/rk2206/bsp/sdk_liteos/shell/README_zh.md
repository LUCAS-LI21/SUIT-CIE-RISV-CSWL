# shell命令

## 1、shell简介

LiteOS提供shell命令行，它能够以命令行交互的方式访问操作系统的功能或服务：它接收并解析用户输入的命令，并处理操作系统的输出结果。

## 2、基础知识

LiteOS提供的Shell作为在线调试工具，可以通过串口工具输入输出，支持常用的基本调试功能。同时用户可以新增定制的命令，新增命令需重新编译烧录后才能执行。

### 2.1、新增Shell命令的开发流程

有静态注册命令和系统运行时动态注册命令两种注册方式。

- 静态注册命令：

  ```c
  #include "shcmd.h"
  SHELLCMD_ENTRY(ls_shellcmd, CMD_TYPE_EX, "ls", XARGS, (CMD_CBK_FUNC)osShellCmdLs);
  ```

- 动态注册命令：

  ```c
  #include "shell.h"
  osCmdReg(CMD_TYPE_EX, "ls", XARGS, (CMD_CBK_FUNC)osShellCmdLs);
  ```

### 2.2、实例：动态注册

**步骤01：定义命令所要调用的命令处理函数cmd_test**

```
#include "shell.h"
#include "shcmd.h"

int cmd_test(void)
{
    printf("hello everybody!\n");
    return 0;
}
```

**步骤02：添加新增命令项**

```
#include "shell.h"
osCmdReg(CMD_TYPE_STD, "test", XARGS, (CMD_CBK_FUNC)cmd_test);
```

## 3、实例：动态注册test

### 3.1、创建文件

在//device/rockchip/rk2206/sdk_liteos/shell创建文件，具体如下：

```
//device/rockchip/rk2206/sdk_liteos/shell
├── include
|   └── shell_test.h		# ifconfig头文件
├── src                       
|   └── shell_test.c		# ifconfig实现的源代码
├── BUILD.gn
```

其中，shell_test.h具体如下：

```c
#ifndef _SHELL_TEST_H_
#define _SHELL_TEST_H_

int shell_test(int argc, const char *argv[]);

#endif // _SHELL_TEST_H_
```

shell_test.c具体如下：

```c
#include <stdio.h>

#if (LOGCFG_SHELL == 1)
#include "shcmd.h"
#include "shell.h"
#endif // LOGCFG_SHELL

int shell_test(int argc, const char *argv[])
{
    printf("Hello World\n");
    return 0;
}
```

### 3.2、注册shell

在//device/rockchip/rk2206/sdk_liteos/shell/shell_cmd.c添加如下代码：

```c
#if (LOGCFG_SHELL == 1)
#include "shcmd.h"
#include "shell.h"
#endif // LOGCFG_SHELL

void shell_cmd_init()
{
#if (LOGCFG_SHELL == 1)
    osCmdReg(CMD_TYPE_STD, "test", 0, (CMD_CBK_FUNC)shell_test);			# 添加该行
    osCmdReg(CMD_TYPE_STD, "uname", 0, (CMD_CBK_FUNC)shell_uname);
    osCmdReg(CMD_TYPE_STD, "hwinfo", XARGS, (CMD_CBK_FUNC)shell_hwinfo);
    osCmdReg(CMD_TYPE_STD, "flash", XARGS, (CMD_CBK_FUNC)shell_flash);
    osCmdReg(CMD_TYPE_STD, "ifconfig", XARGS, (CMD_CBK_FUNC)shell_ifconfig);
#endif // // LOGCFG_SHELL
}
```

### 3.3、参与编译

在//device/rockchip/rk2206/sdk_liteos/shell/BUILD.gn添加如下代码：

```
import("//device/rockchip/rk2206/sdk_liteos/board.gni")
import("//drivers/adapter/khdf/liteos_m/hdf.gni")

static_library("shellcmd") {
  sources = [
    "src/shell_cmd.c",
    "src/shell_test.c",			# 添加该行
    "src/shell_uname.c",
    "src/shell_hwinfo.c",
    "src/shell_flash.c",
    "src/shell_ifconfig.c",
  ]

  include_dirs = [
    "$kernel_path/kernel/include",
    "$kernel_path/kernel/arch/include",
    "$kernel_path/utils",
    "$kernel_path/kal/cmsis",
    "$kernel_path/kal",
    "$kernel_path/components/shell/include",
    "$hilog_path/interfaces/native/kits",
    "$adapter_path/include",
    "include",
    "../board/include",
    "//third_party/cJSON",
    "//third_party/lwip/src/include",
    "//third_party/musl/porting/liteos_m/kernel/include",
    "$rk_third_party_dir/simple_gui/inc",
    "$hdf_fwk_path/include/platform",
    "$hdf_fwk_path/include/utils",
    "$hdf_fwk_path/include/osal",
    "$khdf_path/osal/include",
  ]

  deps = []
}
```





