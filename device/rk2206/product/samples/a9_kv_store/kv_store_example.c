#include "los_task.h"
#include "ohos_init.h"

#include "kv_store.h"

#include <stdio.h>

/* 键值 */
const char key[] = "key_sample";

/***************************************************************
* 函数名称: kv_store_write_thread
* 说    明: KV存储写入线程
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void kv_store_write_thread()
{
    int ret = 0;
    char defValue[50] = {0};
    int current = 0;

    while (1)
    {
        snprintf(defValue, sizeof(defValue), "test value %d.", current);
        int ret = UtilsSetValue(key, defValue);
        if (ret < 0)
        {
            printf("[error] %d\r\n", ret);
        }
        else
        {
            printf("[write] write success\r\n");
        }

        current++;
        LOS_Msleep(1000);
    }
}

/***************************************************************
* 函数名称: kv_store_read_thread
* 说    明: KV存储读取线程
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void kv_store_read_thread()
{
    int ret = 0;
    char value1[50] = {0};

    while (1)
    {
        ret = UtilsGetValue(key, value1, sizeof(value1));
        if (ret < 0)
        {
            printf("[error] %d\r\n", ret);
        }
        else
        {
            printf("[read] key: %s value:%s\r\n", key, value1);
        }

        LOS_Msleep(1000);
    }
}

/***************************************************************
* 函数名称: kv_store_example
* 说    明: KV存储入口函数
* 参    数: 无
* 返 回 值: 无
***************************************************************/
void kv_store_example()
{
    unsigned int thread_id1;
    unsigned int thread_id2;
    TSK_INIT_PARAM_S task1 = {0};
    TSK_INIT_PARAM_S task2 = {0};
    unsigned int ret = LOS_OK;

    task1.pfnTaskEntry = (TSK_ENTRY_FUNC)kv_store_write_thread;
    task1.uwStackSize = 1024 * 10;
    task1.pcName = "kv_store_write_thread";
    task1.usTaskPrio = 25;
    ret = LOS_TaskCreate(&thread_id1, &task1);
    if (ret != LOS_OK)
    {
        printf("Falied to create kv_store_write_thread ret:0x%x\n", ret);
        return;
    }

    task2.pfnTaskEntry = (TSK_ENTRY_FUNC)kv_store_read_thread;
    task2.uwStackSize = 1024 * 10;
    task2.pcName = "kv_store_read_thread";
    task2.usTaskPrio = 25;
    ret = LOS_TaskCreate(&thread_id2, &task2);
    if (ret != LOS_OK)
    {
        printf("Falied to create kv_store_read_thread ret:0x%x\n", ret);
        return;
    }
}

APP_FEATURE_INIT(kv_store_example);
