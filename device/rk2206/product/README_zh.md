# 环境搭建

## 准备

你需要拥有以下环境：

- 虚拟机Ubuntu 版本大于18.04
- git

## 获取源码

右键`在终端中打开`

![](/vendor/isoftstone/rk2206/docs/figures/环境/打开终端.jpg)

使用以下命令获取源码。

```shell
git clone https://gitee.com/isoh_edu/txsmartropenharmony.git
```

## docker编译

### docker安装

```shell
sudo apt install docker.io
```

### 下载docker镜像

```shell
sudo docker pull swr.cn-south-1.myhuaweicloud.com/openharmony-docker/openharmony-docker:1.0.0
```

### 开启docker

进入到我们刚刚下载的源码目录。

```shell
cd isoftstone-rk2206-openharmony3.0lts/
```

![](/vendor/isoftstone/rk2206/docs/figures/环境/进入源码目录.jpg)

输入以下命令，创建docker。

```shell
sudo docker run -it -v /home/user_name/桌面/isoftstone-rk2206-openharmony3.0lts:/home/openharmony swr.cn-south-1.myhuaweicloud.com/openharmony-docker/openharmony-docker:1.0.0
```

`/home/user_name/桌面/isoftstone-rk2206-openharmony3.0lts`是你的源码路径。

### 安装相关工具

```shell
# 下载编译工具
./build/prebuilts_download.sh
# 安装hb工具
pip3 install build/lite
```

### 编译

```shell
hb set -root .
hb set
isoftstone
   isoftstone-rk2206
isoftstone-rk2206
hb build -f
```

编译出的目标文件在 out/rk2206/isoftstone-rk2206/images目录下。

## docker基础操作

### 查看已有docker镜像

```shell
sudo docker container ls -a
```

### 开启镜像

使用`查看已有docker镜像`获取镜像id。

```shell
sudo docker start [docker id]
sudo docker attach [docker id]
```

### 退出镜像

在镜像中输入以下命令，退出镜像。

```shell
exit
```
