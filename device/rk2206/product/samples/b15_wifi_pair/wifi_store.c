#include "wifi_store.h"
#include "kv_store.h"
#include "stdio.h"

//定义kv存储的ssid和pwd的key
#define KV_SSID "ssid"
#define KV_PWD "password"


/*
保存wifi的ssid和密码到kv存储
***/
void saveWifiInfo(char *ssid, char *pwd){
    if (UtilsSetValue(KV_SSID, ssid) !=0){
        printf("store ssid failed! \n");
        return;
    }
    if (UtilsSetValue(KV_PWD, pwd) != 0){
        printf("store password failed! \n");
        return;
    }
    printf("store password success! \n");
}

/**
*清除保存的ssid和密码
 */
void clearWifiInfo(){

    if (UtilsDeleteValue(KV_SSID) !=0){
        printf("clear ssid failed! \n");
        return;
    }
    if (UtilsDeleteValue(KV_PWD) != 0){
        printf("clear password failed! \n");
        return;
    }
    printf("clear password success! \n");
}
/**
*从flash中读取保存的ssid和密码,返回1：找到,0:没找到 */
int loadWifiInfo(char *ssid, char *pwd){

    int not_found=0;

    if(ssid == NULL || pwd == NULL){
        return 0;
    }
    
    //从flash读取保存的ssid
    if (UtilsGetValue(KV_SSID, ssid, WIFI_INFO_MAX_LEN -1) < 0) {
        printf("get ssid value failed!\n");
        not_found = 1;
    }
    printf("===== key: %s, value: %s =====\n", KV_SSID, ssid);

    //从flash读取保存的密码
    if (UtilsGetValue(KV_PWD, pwd, WIFI_INFO_MAX_LEN-1) < 0) {
        printf("get pwd value failed!\n");
        not_found = 1;
    }
    printf("===== key: %s, value: %s =====\n", KV_PWD, pwd);

    printf("==> not_found = %d\n",not_found);

    return !not_found;
}
