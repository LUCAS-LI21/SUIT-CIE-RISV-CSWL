#ifndef  __WIFI_STORE_H__
#define  __WIFI_STORE_H__

#define WIFI_INFO_MAX_LEN 32

int loadWifiInfo(char *ssid, char *pwd);
void saveWifiInfo(char *ssid, char *pwd);
void clearWifiInfo();

#endif

