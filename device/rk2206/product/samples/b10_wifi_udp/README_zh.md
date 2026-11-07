# 通晓开发板基础外设开发——wifi-udp通信

本示例将演示如何在通晓开发板上使用wifi进行udp通信

![通晓开发板](/vendor/isoftstone/rk2206/docs/figures/tx_smart_r-rk2206.jpg)

## WiFi ssid 和密码设置

```c
#define ROUTE_SSID "YOUR_WIFI_SSID"   // WiFi名
#define ROUTE_PASSWORD "YOUR_PASSWORD"   // WiFi密码
```

确保通晓开发板于您的电脑连接同一个WiFi。

## 程序设计

### API分析

#### 头文件

```c
#include "config_network.h"
#include "lwip/tcp.h"
#include "lwip/ip_addr.h"
#include "lwip/priv/tcp_priv.h"
#include "lwip/stats.h"
#include "lwip/inet_chksum.h"
```

#### socket()

```c
int socket(int domain, int type, int protocol)；
```

**描述：**

创建套接字
**参数：**

| 名字          | 描述           |
| :------------ | :------------- |
| domai         |协议类型，一般为AF_INET      |
| type          | socket类型 |
| protocol      | 用来指定socket所使用的传输协议编号，通常设为0即可 |

**返回值：**

返回大于0为成功，反之为失败

#### bind()

```c
int bind(int sockfd, struct sockaddr *my_addr, int addrlen)；
```

**描述：**

绑定地址端口号

**参数：**

| 名字          | 描述           |
| :------------ | :------------- |
| sockfd        | socket描述符         |
| my_addr       | 是一个指向包含有本机ip地址和端口号等信息的sockaddr类型的指针 |
| addrlen       | 常被设为sizeof(struct sockaddr)  |

**返回值：**

返回大于0为成功，反之为失败

#### connect()

```c
int connect(int sockfd, struct sockaddr *serv_addr, int addrlen)；
```

**描述：**

仅仅用于表示确定了另一方的地址

**参数：**

| 名字           | 描述                      |
| :------------ | :------------------------ |
| sockfd        | 目的服务器的socket描述符       |
| serv_addr     | 包含目的机器ip地址和端口号的指针 |
| addrlen       | sizeof(struct sockaddr)     |

**返回值：**

返回大于0为成功，反之为失败

#### sendto()

```c
int sendto(int sockfd, const void *msg, int len, int flags, struct sockaddr * to int *tolen);
```

**描述：**

发送消息

**参数：**

| 名字          | 描述                      |
| :------------ | :------------------------ |
| sockfd        | 用来传输数据的socket描述符 |
| msg           | 要发送数据的指针  |
| len           | 要发送的数据长度（字节）|
| flags         | 0|
| to            | 目的socket描述符|
| tolen         | 目的socket描述符长度|

**返回值：**

发送成功返回发送字节数，失败返回值小于0

#### recvfrom()

```c
int recvfrom(int sockfd, void *buf, int len, unsigned int flags, struct sockaddr * from int *fromlen)；
```

**描述：**

接收消息

**参数：**

| 名字          | 描述                      |
| :------------ | :------------------------ |
| sockfd        | 接收数据的socket描述符 |
| buf           | 存放数据的缓冲区  |
| len           | 缓冲的长度（字节）|
| flags         | 0|
| from          | 来自socket描述符|
| fromlen       | 来自socket描述符长度|

**返回值：**

接收成功返回大于0，失败返回值小于0


#### lwip_close()

```c
int lwip_close(int sockfd)；
```

**描述：**

关闭套接字

**参数：**

| 名字          | 描述                      |
| :------------ | :------------------------ |
| sockfd        | 要关闭的套接字 |

**返回值：**

发送成功返回发送字节数，失败返回值小于0


### 主要代码分析

创建客户端任务 socket-->connect-->send-->recv-->lwip_close
```c
int wifi_udp_client(void* arg)
{
    int client_fd, ret;
    struct sockaddr_in serv_addr, local_addr;
    
    while(1)
    {
        client_fd = socket(AF_INET, SOCK_DGRAM, 0);//AF_INET:IPV4;SOCK_DGRAM:UDP
        if (client_fd < 0)
        {
            printf("create socket fail!\n");
            return -1;
        }

        /*设置调用close(socket)后,仍可继续重用该socket。调用close(socket)一般不会立即关闭socket，而经历TIME_WAIT的过程。*/
        int flag = 1;
        ret = setsockopt(client_fd, SOL_SOCKET, SO_REUSEADDR, &flag, sizeof(int));
        if (ret != 0) {
            printf("[CommInitUdpServer]setsockopt fail, ret[%d]!\n", ret);
        }

        memset(&local_addr, 0, sizeof(local_addr));
        local_addr.sin_family = AF_INET;
        local_addr.sin_addr.s_addr = wifiinfo.ipAddress;
        local_addr.sin_port = htons(CLIENT_LOCAL_PORT);
        //绑定本地ip端口号
        ret = bind(client_fd, (struct sockaddr*)&local_addr, sizeof(local_addr));
              
        memset(&serv_addr, 0, sizeof(serv_addr));
        serv_addr.sin_family = AF_INET;
        serv_addr.sin_addr.s_addr = htonl(INADDR_ANY); //IP地址，需要进行网络序转换，INADDR_ANY：本地地址
        // serv_addr.sin_addr.s_addr = inet_addr(OC_SERVER_IP);  //指定ip接收
        serv_addr.sin_port = htons(SERVER_PORT);

        udp_client_msg_handle(client_fd, (struct sockaddr*)&serv_addr);
        
        LOS_Msleep(1000);
    }

    return 0;
}

```

连接服务端，并发送消息和接收消息
```c

void udp_client_msg_handle(int fd, struct sockaddr* dst)
{
    socklen_t len = sizeof(*dst);

    struct sockaddr_in client_addr;
    int cnt = 0,count = 0;
    printf("[udp client] remote addr:%s port:%u\n", inet_ntoa(((struct sockaddr_in*)dst)->sin_addr), ntohs(((struct sockaddr_in*)dst)->sin_port));
    connect(fd, dst, len);
    getsockname(fd, (struct sockaddr*)&client_addr,&len);
    printf("[udp client] local  addr:%s port:%u\n", inet_ntoa(client_addr.sin_addr), ntohs(client_addr.sin_port));

    while (1)
    {
        char buf[BUFF_LEN];
        printf("[udp client]------------------------------------------------\n");
        printf("[udp client] waitting server message!!!\n");
        // count = recv(fd, buf, BUFF_LEN, 0);       //接收来自server的信息
        count = recvfrom(fd, buf, BUFF_LEN, 0, (struct sockaddr*)&client_addr, &len);       //recvfrom是阻塞函数，没有数据就一直阻塞
        if(count == -1)
        {
            printf("[udp client] No server message!!!\n");
        }
        else
        {
            printf("[udp client] remote addr:%s port:%u\n", inet_ntoa(client_addr.sin_addr), ntohs(client_addr.sin_port));
            printf("[udp client] rev:%s\n", buf);
        }
        memset(buf, 0, BUFF_LEN);
        sprintf(buf, "UDP TEST cilent send:%d", ++cnt);
        // count = send(fd, buf, strlen(buf), 0);                      //发送数据给server
        count = sendto(fd, buf, strlen(buf), 0, (struct sockaddr*)&client_addr, len);        //发送信息给client
        printf("[udp client] send:%s\n", buf);
        printf("[udp client] client sendto msg to server %dbyte,waitting server respond msg!!!\n", count);

        LOS_Msleep(100);
    }
    lwip_close(fd);
}
```


创建服务端任务 socket-->bind-->listen-->accept-->recv-->send-->close
```c
int wifi_udp_server(void* arg)
{
    int server_fd, ret;

    while(1)
    {
        server_fd = socket(AF_INET, SOCK_DGRAM, 0); //AF_INET:IPV4;SOCK_DGRAM:UDP
        if (server_fd < 0)
        {
            printf("create socket fail!\n");
            return -1;
        }

        /*设置调用close(socket)后,仍可继续重用该socket。调用close(socket)一般不会立即关闭socket，而经历TIME_WAIT的过程。*/
        int flag = 1;
        ret = setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &flag, sizeof(int));
        if (ret != 0) {
            printf("[CommInitUdpServer]setsockopt fail, ret[%d]!\n", ret);
        }
        
        struct sockaddr_in serv_addr = {0};
        serv_addr.sin_family = AF_INET;
        serv_addr.sin_addr.s_addr = htonl(INADDR_ANY); //IP地址，需要进行网络序转换，INADDR_ANY：本地地址
        // serv_addr.sin_addr.s_addr = wifiinfo.ipAddress; 
        serv_addr.sin_port = htons(SERVER_PORT);       //端口号，需要网络序转换
        /* 绑定服务器地址结构 */
        ret = bind(server_fd, (struct sockaddr*)&serv_addr, sizeof(serv_addr));
        if (ret < 0)
        {
            printf("socket bind fail!\n");
            lwip_close(server_fd);
            return -1;
        }
        printf("[udp server] local  addr:%s,port:%u\n", inet_ntoa(wifiinfo.ipAddress), ntohs(serv_addr.sin_port));

        udp_server_msg_handle(server_fd);   //处理接收到的数据
        LOS_Msleep(1000);
    }
}
```

连接客户端，并发送消息和接收消息
```c
void udp_server_msg_handle(int fd)
{
    char buf[BUFF_LEN];  //接收缓冲区
    socklen_t len;
    int cnt = 0, count;
    struct sockaddr_in client_addr = {0};
    while (1)
    {
        memset(buf, 0, BUFF_LEN);
        len = sizeof(client_addr);
        printf("[udp server]------------------------------------------------\n");
        printf("[udp server] waitting client msg\n");
        count = recvfrom(fd, buf, BUFF_LEN, 0, (struct sockaddr*)&client_addr, &len);       //recvfrom是阻塞函数，没有数据就一直阻塞
        if (count == -1)
        {
            printf("[udp server] recieve data fail!\n");
            LOS_Msleep(3000);
            break;
        }
        printf("[udp server] remote addr:%s port:%u\n", inet_ntoa(client_addr.sin_addr), ntohs(client_addr.sin_port));
        printf("[udp server] rev client msg:%s\n", buf);
        memset(buf, 0, BUFF_LEN);
        sprintf(buf, "I have recieved %d bytes data! recieved cnt:%d", count, ++cnt);
        printf("[udp server] send msg:%s\n", buf);
        sendto(fd, buf, strlen(buf), 0, (struct sockaddr*)&client_addr, len);        //发送信息给client
    }
    lwip_close(fd);
}

```

## 编译调试

### 修改 BUILD.gn 文件

修改 `vendor\isoftstone\rk2206\sample 路径下 BUILD.gn 文件，指定 `wifi_udp_example` 参与编译。

```r
"./b10_wifi_udp:wifi_udp_example",
```

修改 `device/rockchip/rk2206/sdk_liteos` 路径下 Makefile 文件，添加 `-lwifi_udp_example` 参与编译。

```r
hardware_LIBS = -lhal_iothardware -lhardware -lwifi_udp_example
```

### 运行结果

示例代码编译烧录代码后，按下开发板的RESET按键，通过串口助手查看日志，串口显示如下：

```c
entering kernel init...
hilog will init.
[IOT:D]IotInit: start ....
[MAIN:D]Main: LOS_Start ...
Entering scheduler
[IOT:D]IotProcess: start ....
IotProcess: sleep 5 sec!
wifi_udp_example start ....
[FLASH:I]FlashInit: blockSize 4096, blockStart 0, blockEnd 8388608
[config_network:D]rknetwork wifi is inactive

[FLASH:E]FlashInit: id 0, controller has already been initialized
[config_network:D]rknetwork SetWifiModeOn

[config_network:D]rknetwork g_wificonfig.ssid 软通教育

[config_network:D]rknetwork g_wificonfig.psk 88888888

[wifi_api:D]ip=192.168.2.10 gw=192.168.2.1 mask=255.255.255.0
[wifi_api:D]HWADDR (00:dc:b6:90:00:00)
[bcore_device:E]start bb ...
[bcore_device:E]start bb done
[wifi_api:D]netif setup ...
[config_network:D]rknetwork EnableWifi done

[config_network:D]rknetwork SetWifiScan after g_wificonfig.bssid:

[wifi_api_internal:D]Connect to (SSID=软通教育)
[wifi_api_internal:D]derive psk ...
[wifi_api_internal:D]derive psk done
IotProcess: sleep 5 sec!
[wifi_api_internal:D]recovery process ...
IotProcess: sleep 5 sec!
[wifi_api_internal:D]AP BSSID (30:5f:77:80:80:b0)
[config_network:I]ConnectTo (软通教育) done
[config_network:D]rknetwork IP (192.168.2.30)
[config_network:D]network GW (192.168.2.1)
[config_network:D]network NETMASK (255.255.255.0)
[WIFI_DEVICE:E]l o num:0 127.0.0.1
[WIFI_DEVICE:E]w l num:1 192.168.2.30
[config_network:D]set network GW
[config_network:D]network GW (192.168.2.1)
[config_network:D]network NETMASK (255.255.255.0)
[udp:]rknetwork IP (192.168.2.30)
[udp:]network GW (192.168.2.1)
[udp:]network NETMASK (255.255.255.0)
[WIFI_DEVICE:E]l o num:0 127.0.0.1
[WIFI_DEVICE:E]w l num:1 192.168.2.30
[udp:]set network GW
[udp:]network GW (192.168.2.1)
[udp:]network NETMASK (255.255.255.0)
[udp client] remote addr:0.0.0.0 port:6666
[udp client] local  addr:192.168.2.30 port:8888
[udp client]------------------------------------------------
[udp client] waitting server message!!!
[udp server] local  addr:192.168.2.30,port:6666
[udp server]------------------------------------------------
[udp server] waitting client message!!!
```

通过网络调试助手发送消息，通晓开发板会打印以下log。

![网络调试工具 tcp_msg](/vendor/isoftstone/rk2206/docs/figures/udp/发送消息.jpg)

```c
[udp server] remote addr:192.168.2.62 port:6666
[udp server] rev:Hello World!
[udp server] send:I have recieved 12 bytes data! recieved cnt:2
[udp server]------------------------------------------------
[udp server] waitting client message!!!

```
