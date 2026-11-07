#include "ohos_init.h"
#include "cmsis_os2.h"
#include "los_task.h"
#include "config_network.h"
#include "lwip/tcp.h"
#include "lwip/ip_addr.h"
#include "lwip/priv/tcp_priv.h"
#include "lwip/stats.h"
#include "lwip/inet_chksum.h"
#include "wifi_store.h"
#include "button.h"

#define LOG_TAG    "udp"

#define TX_LOG(tag, fmt, ...)  do { \
    printf("[" tag ":]" fmt "\n", ##__VA_ARGS__); \
} while (0)

int get_wifi_info(WifiLinkedInfo *info);

#define OC_SERVER_IP   "YOUR_SERVER_IP"
#define SERVER_PORT        6666


static   char g_ssid[WIFI_INFO_MAX_LEN]={0};
static   char g_pwd[WIFI_INFO_MAX_LEN]={0};
WifiLinkedInfo wifiinfo;

int get_wifi_info(WifiLinkedInfo *info)
{
    int ret = -1;
    int gw, netmask;
    memset(info, 0, sizeof(WifiLinkedInfo));
    unsigned int retry = 15;
    while (retry) {
        if (GetLinkedInfo(info) == WIFI_SUCCESS) {
            if (info->connState == WIFI_CONNECTED) {
                if (info->ipAddress != 0) {
                    TX_LOG(LOG_TAG, "rknetwork IP (%s)", inet_ntoa(info->ipAddress));
                    if (WIFI_SUCCESS == GetLocalWifiGw(&gw)) {
                        TX_LOG(LOG_TAG, "network GW (%s)", inet_ntoa(gw));
                    }
                    if (WIFI_SUCCESS == GetLocalWifiNetmask(&netmask)) {
                        TX_LOG(LOG_TAG, "network NETMASK (%s)", inet_ntoa(netmask));
                    }
                    if (WIFI_SUCCESS == SetLocalWifiGw()) {
                        TX_LOG(LOG_TAG, "set network GW");
                    }
                    if (WIFI_SUCCESS == GetLocalWifiGw(&gw)) {
                        TX_LOG(LOG_TAG, "network GW (%s)", inet_ntoa(gw));
                    }
                    if (WIFI_SUCCESS == GetLocalWifiNetmask(&netmask)) {
                        TX_LOG(LOG_TAG, "network NETMASK (%s)", inet_ntoa(netmask));
                    }
                    ret = 0;
                    goto connect_done;
                }
            }
        }
        LOS_Msleep(1000);
        retry--;
    }

connect_done:
    return ret;
}


void udp_server_msg_handle(int fd)
{
    char recvBuf[512] = { 0 };
    char sendBuf[512] = { 0 };
    socklen_t len;
    int ret = 0;
    int count = 0;
    struct sockaddr_in client_addr = {0};

    while (1)
    {
        memset(recvBuf, 0, sizeof(recvBuf));
        len = sizeof(client_addr);
        printf("[udp server]------------------------------------------------\n");
        printf("[udp server] waitting client message!!!\n");
        count = recvfrom(fd, recvBuf, sizeof(recvBuf), 0, (struct sockaddr*)&client_addr, &len);       //recvfrom是阻塞函数，没有数据就一直阻塞
        if (count == -1)
        {
            printf("[udp server] recieve data fail!\n");
            LOS_Msleep(3000);
            break;
        }
        printf("[udp server] remote addr:%s port:%u\n", inet_ntoa(client_addr.sin_addr), ntohs(client_addr.sin_port));
        printf("[udp server] rev:%s\n", recvBuf);
        
        /**
        *解析收到的数据,格式:ssid:xxxx,pwd:yyyy
        */
        char *ssid_str = strstr(recvBuf,"ssid:");
        char *comma_str = strstr(recvBuf,",pwd:");

        if((ssid_str != NULL) && (comma_str != NULL)){
            char ssid[32]={0};
            char pwd[32]={0};

            memcpy(ssid,ssid_str+5,comma_str-(ssid_str+5));
            memcpy(pwd,comma_str+5,recvBuf+strlen(recvBuf)-(comma_str+5));

            printf("==>> {{{%s,%s}}}}\n",ssid,pwd);

            sprintf(sendBuf,"OK ==>%s \n",recvBuf);
            ret = sendto(fd, sendBuf, strlen(sendBuf), 0, 
                (struct sockaddr *)&client_addr, sizeof(client_addr));
            if (ret < 0) {
                printf("UDP server send failed!\r\n");
                return -1;
            }
            //保存ssid和密码信息
            saveWifiInfo(ssid,pwd);
            //重启设备
            RebootDevice(3);
        }

    }
    lwip_close(fd);
}

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

        /*设置调用close(socket)后,仍可继续重用该socket。
        调用close(socket)一般不会立即关闭socket，而经历TIME_WAIT的过程。*/
        int flag = 1;
        ret = setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &flag, sizeof(int));
        if (ret != 0) {
            printf("[CommInitUdpServer]setsockopt fail, ret[%d]!\n", ret);
        }
        
        struct sockaddr_in serv_addr = {0};
        serv_addr.sin_family = AF_INET;
        //IP地址，需要进行网络序转换，INADDR_ANY：本地地址
        serv_addr.sin_addr.s_addr = htonl(INADDR_ANY); 
         //端口号，需要网络序转换
        serv_addr.sin_port = htons(SERVER_PORT);      
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

int AP_task(WifiLinkedInfo *info)
{
    printf(">>start ap mode!\n");
    set_wifi_config_ssid(printf, "MY_AP");
    set_wifi_config_passwd(printf, "12345678");
    set_wifi_config_mode(printf, "AP");
    SetApModeOn();

    wifi_udp_server(NULL);
}


void sta_process(void *args)
{
    unsigned int ret = LOS_OK;
   
    WifiLinkedInfo info;

    uint8_t mac_address[6] = {0x00, 0xdc, 0xb6, 0x90, 0x00, 0x00};
    FlashInit();

     set_wifi_config_mac(printf, mac_address);
     set_wifi_config_route_ssid(printf, g_ssid);
     set_wifi_config_route_passwd(printf, g_pwd);

    SetWifiModeOff();
    SetWifiModeOn();

    while(get_wifi_info(&info) != 0) ;

    while(1)
    {
        //联网成功之后 运行正常的AP模式程序,比如物联网IoT
        printf("sta mode...");
        LOS_Msleep(1000);
    }
}

void wifi_pair_example(void)
{
    unsigned int ret = LOS_OK;
    unsigned int thread_id;
    TSK_INIT_PARAM_S task = {0};
    printf("%s start ....\n", __FUNCTION__);

    task.pfnTaskEntry = (TSK_ENTRY_FUNC)key_process;
    task.uwStackSize = 10240;
    task.pcName = "key_process";
    task.usTaskPrio = 24;
    ret = LOS_TaskCreate(&thread_id, &task);
    if (ret != LOS_OK)
    {
        printf("Falied to create AP_task ret:0x%x\n", ret);
        return;
    }
    int find = loadWifiInfo(g_ssid,g_pwd);

    if(find == 0){
        task.pfnTaskEntry = (TSK_ENTRY_FUNC)AP_task;
        task.uwStackSize = 10240;
        task.pcName = "wifi_ap";
        task.usTaskPrio = 24;
        ret = LOS_TaskCreate(&thread_id, &task);
        if (ret != LOS_OK)
        {
            printf("Falied to create AP_task ret:0x%x\n", ret);
            return;
        }
        return;
    }else{
        task.pfnTaskEntry = (TSK_ENTRY_FUNC)sta_process;
        task.uwStackSize = 10240;
        task.pcName = "sta_process";
        task.usTaskPrio = 24;
        ret = LOS_TaskCreate(&thread_id, &task);
        if (ret != LOS_OK)
        {
            printf("Falied to create AP_task ret:0x%x\n", ret);
            return;
        }
        return;
    }

}

APP_FEATURE_INIT(wifi_pair_example);

