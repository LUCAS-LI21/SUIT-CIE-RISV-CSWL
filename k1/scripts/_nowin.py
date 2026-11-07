# -*- coding: utf-8 -*-
"""Windows 下「绝不弹控制台」的子进程补丁。

为什么需要
  自愈引导跑在 pythonw（没有控制台）里，但它会周期性调用 hdc.exe 做体检。
  hdc.exe 是**控制台程序**：父进程没有控制台时，Windows 会给它新建一个，
  于是每 8 秒闪一个黑窗口——用户看到的就是「命令行一直在跳」。

用法
  import _nowin            # 副作用式：导入即生效
  （必须在任何 subprocess 调用之前导入）

它把 subprocess.run / subprocess.Popen 兜了一层，默认塞入
CREATE_NO_WINDOW(0x08000000)，任何调用方都不必再操心。
"""
import os
import subprocess

_CREATE_NO_WINDOW = 0x08000000

if os.name == "nt":
    _orig_run = subprocess.run
    _orig_popen = subprocess.Popen

    def _run(*args, **kwargs):
        kwargs.setdefault("creationflags", _CREATE_NO_WINDOW)
        return _orig_run(*args, **kwargs)

    class _Popen(_orig_popen):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("creationflags", _CREATE_NO_WINDOW)
            super().__init__(*args, **kwargs)

    subprocess.run = _run
    subprocess.Popen = _Popen
