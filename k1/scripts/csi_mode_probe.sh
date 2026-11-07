#!/bin/sh
# 在 K1 上探测 IMX219 的 sensor 工作模式 / ISP 输出分辨率组合，评估 720p binning 可行性。
#
# 用法（在 K1 板端）：
#   sh /data/local/tmp/csi_mode_probe.sh            # 只枚举，不实测
#   sh /data/local/tmp/csi_mode_probe.sh test       # 枚举 + 逐组合实测帧率
#
# ⚠️ cam-test 会独占 camera pipeline，脚本会先停 k1-vision-csi，结束时恢复。
# ⚠️ 每个组合限时 12s，跑完一定要看最后有没有 "restore ok"。

JSON=/usr/share/camera_json/csi3_camera_auto.json
BAK=/root/csi3_camera_auto.json.probe-bak
SVC=k1-vision-csi

echo "=========== 1. 当前配置 ==========="
cat "$JSON" 2>/dev/null || { echo "找不到 $JSON"; exit 1; }

echo
echo "=========== 2. sensor 库里的 imx219 模式表 ==========="
# 模式分辨率固化在 xxx_spm_*_setting.h 编进 so 里，tab 名能直接看出分辨率与帧率
found=0
for lib in /usr/lib/libcam_sensors.so /usr/lib64/libcam_sensors.so \
           /usr/lib/libcam_sensor.so /vendor/lib*/libcam_sensors.so; do
    [ -f "$lib" ] || continue
    found=1
    echo "--- $lib"
    strings "$lib" | grep -iE "imx219.*(tab|setting|fps|[0-9]{3,4}x[0-9]{3,4})" | sort -u | head -40
done
[ "$found" = 0 ] && echo "没找到 libcam_sensors.so，用 find 再查一次：" && \
    find / -name "libcam_sensor*.so" 2>/dev/null | head -5

echo
echo "=========== 3. 系统里有没有现成的多模式 json ==========="
ls -la /usr/share/camera_json/ 2>/dev/null

[ "$1" != "test" ] && {
    echo
    echo "（只枚举。加 test 参数可实测各模式帧率）"
    exit 0
}

echo
echo "=========== 4. 实测（停服务，独占 pipeline）==========="
systemctl stop "$SVC" 2>/dev/null && echo "已停 $SVC"
sleep 2
cp "$JSON" "$BAK" || exit 1

probe() {
    # probe <sensor_work_mode> <out_w> <out_h>
    m=$1; ow=$2; oh=$3
    sed -i "s/\"sensor_work_mode\"[[:space:]]*:[[:space:]]*[0-9]*/\"sensor_work_mode\":$m/" "$JSON"
    sed -i "s/\"out_width\"[[:space:]]*:[[:space:]]*[0-9]*/\"out_width\":$ow/" "$JSON"
    sed -i "s/\"out_height\"[[:space:]]*:[[:space:]]*[0-9]*/\"out_height\":$oh/" "$JSON"
    printf 'mode=%s out=%sx%s  -> ' "$m" "$ow" "$oh"
    # cam-test 默认跑几百帧，12s 足够看出成败与帧率
    out=$(timeout 12 cam-test "$JSON" 2>&1)
    if echo "$out" | grep -qiE "fail|error|timeout"; then
        echo "失败: $(echo "$out" | grep -iE 'fail|error' | head -1 | cut -c1-90)"
    else
        fps=$(echo "$out" | grep -oE "[0-9]+\.[0-9]+ ?fps|[Ff][Pp][Ss][ :=]+[0-9]+\.[0-9]+" | tail -1)
        echo "成功  ${fps:-（未打印帧率，看原始日志）}"
    fi
}

# 三个候选：当前 1080p、2x2 binning、以及 720p 输出
# 先固定 out 与 sensor 同尺寸，排除 ISP 缩放这个变量
probe 0 1920 1080
probe 1 1920 1080
probe 4 1640 1232
probe 4 1280 720
probe 6 1280 720

cp "$BAK" "$JSON"
echo "已恢复原始 json"
systemctl start "$SVC" 2>/dev/null && echo "已重启 $SVC"
echo "restore ok"
