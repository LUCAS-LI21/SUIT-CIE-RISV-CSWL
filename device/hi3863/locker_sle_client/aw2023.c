#include "aw2023.h"
#include "cmsis_os2.h"
#include "iot_errno.h"
#include "iot_i2c.h"
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define AW2023_DRIVER_VERSION "v1.0.0"

/* This is just a rough delay */
void delay_ms(AW_U32 ms)
{
    osDelay(ms);
}

AW_U8 aw2023_i2c_write_reg(AW_U8 reg_addr, AW_U8 reg_data)
{
    AW_U8 res = 0;
    AW_U8 cnt = 0;
    uint8_t buffer[2] = {reg_addr, reg_data};

    while (cnt < I2C_RETRY_TIMES)
    {
        res = IoTI2cWrite(AW2023_I2C_IDX, AW2023_DEVICE_ADDR, buffer, 2);
        if (res != IOT_SUCCESS)
        {
            AWLOG("i2c_write cnt = %d, error = %d\n", cnt, res);
        }
        else
        {
            break;
        }
        cnt++;
    }

    return (res == IOT_SUCCESS) ? AW_I2C_OK : AW_I2C_ERROR;
}

AW_U8 aw2023_i2c_read_reg(AW_U8 reg_addr, AW_U8 *reg_data)
{
    AW_U8 res = 0;
    AW_U8 cnt = 0;

    if (reg_data == NULL)
    {
        return AW_I2C_ERROR;
    }

    while (cnt < I2C_RETRY_TIMES)
    {
        res = IoTI2cWrite(AW2023_I2C_IDX, AW2023_DEVICE_ADDR, &reg_addr, 1);
        if (res != IOT_SUCCESS)
        {
            AWLOG("i2c_write reg addr failed, cnt = %d, error = %d\n", cnt, res);
            cnt++;
            continue;
        }

        res = IoTI2cRead(AW2023_I2C_IDX, AW2023_DEVICE_ADDR, reg_data, 1);
        if (res != IOT_SUCCESS)
        {
            AWLOG("i2c_read cnt = %d, error = %d\n", cnt, res);
        }
        else
        {
            break;
        }
        cnt++;
    }

    return (res == IOT_SUCCESS) ? AW_I2C_OK : AW_I2C_ERROR;
}

/* aw2023 software reset */
void aw2023_soft_rst(void)
{
    aw2023_i2c_write_reg(AW2023_REG_RESET, AW2023_RESET_MASK);
    /* delay 5ms at least */
    delay_ms(1);
}

/* aw2023 chip enable */
void aw2023_chip_enable(AW_BOOL enable)
{
    AW_U8 reg_val = 0;

    aw2023_i2c_read_reg(AW2023_REG_GCR1, &reg_val);
    reg_val &= ~AW2023_CHIPEN_MASK;
    reg_val |= (((AW_U8)enable << AW2023_CHIPEN_POS) & AW2023_CHIPEN_MASK);
    aw2023_i2c_write_reg(AW2023_REG_GCR1, reg_val);

    /* About 5ms delay is required after chip enable */
    if (enable == AW_TRUE)
    {
        delay_ms(1);
    }
}

/* aw2023 check chipid */
AW_BOOL aw2023_read_chipid(void)
{
    AW_U8 reg_val = 0;
    AW_BOOL ret = AW_FALSE;

    aw2023_i2c_read_reg(AW2023_REG_RESET, &reg_val);
    AWLOG("chip_id = 0x%x\n", reg_val);
    if (reg_val == AW2023_CHIP_ID)
    {
        printf("read same id\n");
        ret = AW_TRUE;
        printf("ret:%d\n", ret);
    }

    return ret;
}

/* aw2023 global max output current selection */
void aw2023_set_imax(AW_U8 imax)
{
    AW_U8 reg_val = 0;

    aw2023_i2c_read_reg(AW2023_REG_GCR2, &reg_val);
    reg_val &= ~AW2023_IMAX_MASK;
    reg_val |= ((imax << AW2023_IMAX_POS) & AW2023_IMAX_MASK);
    aw2023_i2c_write_reg(AW2023_REG_GCR2, reg_val);
}

/* aw2023 led enable control */
void aw2023_led_enable_ctrl(AW_U8 led_mask, AW_BOOL led_enable)
{
    AW_U8 reg_val = 0;

    aw2023_i2c_read_reg(AW2023_REG_LCTR, &reg_val);
    if (led_enable == AW_TRUE)
    {
        reg_val |= ((led_mask << AW2023_LED_ENABLE_CTRL_POS) & AW2023_LED_ENABLE_CTRL_MASK);
    }
    else
    {
        reg_val &= ~((led_mask << AW2023_LED_ENABLE_CTRL_POS) & AW2023_LED_ENABLE_CTRL_MASK);
    }
    aw2023_i2c_write_reg(AW2023_REG_LCTR, reg_val);
}

/* aw2023 pwm transition mode selection: exponential or linear */
void aw2023_set_pwm_mode(AW_U8 mode)
{
    AW_U8 reg_val = 0;

    aw2023_i2c_read_reg(AW2023_REG_LCTR, &reg_val);
    reg_val &= ~AW2023_PWM_MODE_SELECTION_MASK;
    reg_val |= ((mode << AW2023_PWM_MODE_SELECTION_POS) & AW2023_PWM_MODE_SELECTION_MASK);
    aw2023_i2c_write_reg(AW2023_REG_LCTR, reg_val);
}

/* aw2023 pwm carrier frequency selection: 250Hz or 125Hz */
void aw2023_set_pwm_freq(AW_U8 freq)
{
    AW_U8 reg_val = 0;

    aw2023_i2c_read_reg(AW2023_REG_LCTR, &reg_val);
    reg_val &= ~AW2023_PWM_FREQ_MASK;
    reg_val |= ((freq << AW2023_PWM_FREQ_POS) & AW2023_PWM_FREQ_MASK);
    aw2023_i2c_write_reg(AW2023_REG_LCTR, reg_val);
}

/* aw2023 set individual control mode or sync control mode */
void aw2023_set_sync_mode(AW_BOOL sync_enable)
{
    AW_U8 reg_val = 0;

    aw2023_i2c_read_reg(AW2023_REG_LCFG0, &reg_val);
    reg_val &= ~AW2023_LED_SYNC_MASK;
    reg_val |= (((AW_U8)sync_enable << AW2023_LED_SYNC_POS) & AW2023_LED_SYNC_MASK);
    aw2023_i2c_write_reg(AW2023_REG_LCFG0, reg_val);
}

/* aw2023 set led output current */
void aw2023_set_led_current(AW_U8 led_index, AW_U8 current)
{
    AW_U8 reg_val = 0;

    if (led_index < AW2023_LED_NUM)
    {
        aw2023_i2c_read_reg(AW2023_REG_LCFG0 + led_index, &reg_val);
        reg_val &= ~AW2023_LED_CUR_MASK;
        reg_val |= ((current << AW2023_LED_CUR_POS) & AW2023_LED_CUR_MASK);
        aw2023_i2c_write_reg(AW2023_REG_LCFG0 + led_index, reg_val);
    }
}

/* aw2023 led operating mode select: manual mode or pattern mode */
void aw2023_set_led_operating_mode(AW_U8 led_index, AW_U8 mode)
{
    AW_U8 reg_val = 0;

    if (led_index < AW2023_LED_NUM)
    {
        aw2023_i2c_read_reg(AW2023_REG_LCFG0 + led_index, &reg_val);
        reg_val &= ~AW2023_LED_MD_MASK;
        reg_val |= ((mode << AW2023_LED_MD_POS) & AW2023_LED_MD_MASK);
        aw2023_i2c_write_reg(AW2023_REG_LCFG0 + led_index, reg_val);
    }
}

/* aw2023 fade-in enable control, only active in manual mode */
void aw2023_fade_in_enable_ctrl(AW_U8 led_index, AW_BOOL enable)
{
    AW_U8 reg_val = 0;

    if (led_index < AW2023_LED_NUM)
    {
        aw2023_i2c_read_reg(AW2023_REG_LCFG0 + led_index, &reg_val);
        reg_val &= ~AW2023_LED_FI_MASK;
        reg_val |= (((AW_U8)enable << AW2023_LED_FI_POS) & AW2023_LED_FI_MASK);
        aw2023_i2c_write_reg(AW2023_REG_LCFG0 + led_index, reg_val);
    }
}

/* aw2023 fade-out enable control, only active in manual mode */
void aw2023_fade_out_enable_ctrl(AW_U8 led_index, AW_BOOL enable)
{
    AW_U8 reg_val = 0;

    if (led_index < AW2023_LED_NUM)
    {
        aw2023_i2c_read_reg(AW2023_REG_LCFG0 + led_index, &reg_val);
        reg_val &= ~AW2023_LED_FO_MASK;
        reg_val |= (((AW_U8)enable << AW2023_LED_FO_POS) & AW2023_LED_FO_MASK);
        aw2023_i2c_write_reg(AW2023_REG_LCFG0 + led_index, reg_val);
    }
}

/* aw2023 set led pwm dimming level */
void aw2023_set_pwm_dimming(AW_U8 led_index, AW_U8 dim)
{
    if (led_index < AW2023_LED_NUM)
    {
        aw2023_i2c_write_reg(AW2023_REG_PWM0 + led_index, dim);
    }
}

/* aw2023 pattern or manual mode rise-time */
void aw2023_set_rise_time(AW_U8 led_index, AW_U8 time)
{
    AW_U8 reg_val = 0;

    if (led_index < AW2023_LED_NUM)
    {
        aw2023_i2c_read_reg(AW2023_REG_LED0T0 + led_index * AW2023_LED_NUM, &reg_val);
        reg_val &= ~AW2023_RISE_TIME_MASK;
        reg_val |= ((time << AW2023_RISE_TIME_POS) & AW2023_RISE_TIME_MASK);
        aw2023_i2c_write_reg(AW2023_REG_LED0T0 + led_index * AW2023_LED_NUM, reg_val);
    }
}

/* aw2023 pattern mode on-time */
void aw2023_set_on_time(AW_U8 led_index, AW_U8 time)
{
    AW_U8 reg_val = 0;

    if (led_index < AW2023_LED_NUM)
    {
        aw2023_i2c_read_reg(AW2023_REG_LED0T0 + led_index * AW2023_LED_NUM, &reg_val);
        reg_val &= ~AW2023_ON_TIME_MASK;
        reg_val |= ((time << AW2023_ON_TIME_POS) & AW2023_ON_TIME_MASK);
        aw2023_i2c_write_reg(AW2023_REG_LED0T0 + led_index * AW2023_LED_NUM, reg_val);
    }
}

/* aw2023 pattern or manual mode fall-time */
void aw2023_set_fall_time(AW_U8 led_index, AW_U8 time)
{
    AW_U8 reg_val = 0;

    if (led_index < AW2023_LED_NUM)
    {
        aw2023_i2c_read_reg(AW2023_REG_LED0T1 + led_index * AW2023_LED_NUM, &reg_val);
        reg_val &= ~AW2023_FALL_TIME_MASK;
        reg_val |= ((time << AW2023_FALL_TIME_POS) & AW2023_FALL_TIME_MASK);
        aw2023_i2c_write_reg(AW2023_REG_LED0T1 + led_index * AW2023_LED_NUM, reg_val);
    }
}

/* aw2023 pattern mode off-time */
void aw2023_set_off_time(AW_U8 led_index, AW_U8 time)
{
    AW_U8 reg_val = 0;

    if (led_index < AW2023_LED_NUM)
    {
        aw2023_i2c_read_reg(AW2023_REG_LED0T1 + led_index * AW2023_LED_NUM, &reg_val);
        reg_val &= ~AW2023_OFF_TIME_MASK;
        reg_val |= ((time << AW2023_OFF_TIME_POS) & AW2023_OFF_TIME_MASK);
        aw2023_i2c_write_reg(AW2023_REG_LED0T1 + led_index * AW2023_LED_NUM, reg_val);
    }
}

/* aw2023 delay time of pattern startup */
void aw2023_set_delay_time(AW_U8 led_index, AW_U8 time)
{
    AW_U8 reg_val = 0;

    if (led_index < AW2023_LED_NUM)
    {
        aw2023_i2c_read_reg(AW2023_REG_LED0T2 + led_index * AW2023_LED_NUM, &reg_val);
        reg_val &= ~AW2023_DELAY_TIME_MASK;
        reg_val |= ((time << AW2023_DELAY_TIME_POS) & AW2023_DELAY_TIME_MASK);
        aw2023_i2c_write_reg(AW2023_REG_LED0T2 + led_index * AW2023_LED_NUM, reg_val);
    }
}

/* aw2023 pattern repeat time */
void aw2023_set_repeat_time(AW_U8 led_index, AW_U8 time)
{
    AW_U8 reg_val = 0;

    if (led_index < AW2023_LED_NUM)
    {
        aw2023_i2c_read_reg(AW2023_REG_LED0T2 + led_index * AW2023_LED_NUM, &reg_val);
        reg_val &= ~AW2023_REPEAT_TIME_MASK;
        reg_val |= ((time << AW2023_REPEAT_TIME_POS) & AW2023_REPEAT_TIME_MASK);
        aw2023_i2c_write_reg(AW2023_REG_LED0T2 + led_index * AW2023_LED_NUM, reg_val);
    }
}

/* aw2023 chip init */
AW_BOOL aw2023_init(void)
{
    AW_BOOL ret = AW_FALSE;

    /* 1.Soft reset */
    aw2023_soft_rst();
    /* 2.Chip enable */
    aw2023_chip_enable(AW_TRUE);
    /* 3.Read and check that the chip ID is correct */
    ret = aw2023_read_chipid();
    if (ret == AW_TRUE)
    {
        /* 4.Set the global maximum output current */
        aw2023_set_imax(AW2023_IMAX_15MA);
        /* 5.Set the PWM output frequency */
        aw2023_set_pwm_freq(AW2023_PWM_FREQ_250HZ);
    }

    return ret;
}

/* aw2023 all led full on */
AW_BOOL aw2023_all_led_full_on(void)
{
    AW_BOOL ret = AW_FALSE;

    /* 1.Chip initialization */
    ret = aw2023_init();
    if (ret == AW_TRUE)
    {
        /* 2.Select pwm transition mode */
        aw2023_set_pwm_mode(AW2023_PWM_LINEAR);
        /* 3.Set the LED port output current */
        aw2023_set_led_current(AW2023_LED0, 0x0f);
        aw2023_set_led_current(AW2023_LED1, 0x0f);
        aw2023_set_led_current(AW2023_LED2, 0x0f);
        /* 4.Set led pwm dimming level */
        aw2023_set_pwm_dimming(AW2023_LED0, 0xff);
        aw2023_set_pwm_dimming(AW2023_LED1, 0xff);
        aw2023_set_pwm_dimming(AW2023_LED2, 0xff);
        /* 5.Three LED channels were enabled */
        aw2023_led_enable_ctrl(AW2023_LED0_EN | AW2023_LED1_EN | AW2023_LED2_EN, AW_TRUE);
    }

    return ret;
}

/* aw2023 all led breath (pattern mode) */
AW_BOOL aw2023_all_led_breath(void)
{
    AW_BOOL ret = AW_FALSE;

    /* 1.Chip initialization */
    ret = aw2023_init();
    if (ret == AW_TRUE)
    {
        /* 2.Select pwm transition mode */
        aw2023_set_pwm_mode(AW2023_PWM_EXPONENTIAL);
        /* 3.Set the LED port output current */
        aw2023_set_led_current(AW2023_LED0, 0x0f);
        aw2023_set_led_current(AW2023_LED1, 0x0f);
        aw2023_set_led_current(AW2023_LED2, 0x0f);
        /* 4.Set led pwm dimming level */
        aw2023_set_pwm_dimming(AW2023_LED0, 0xff);
        aw2023_set_pwm_dimming(AW2023_LED1, 0xff);
        aw2023_set_pwm_dimming(AW2023_LED2, 0xff);
        /* 5.Set rise-time */
        aw2023_set_rise_time(AW2023_LED0, AW2023_PAT_TIME_1_04);
        aw2023_set_rise_time(AW2023_LED1, AW2023_PAT_TIME_2_10);
        aw2023_set_rise_time(AW2023_LED2, AW2023_PAT_TIME_3_10);
        /* 6.Set on-time */
        aw2023_set_on_time(AW2023_LED0, AW2023_PAT_TIME_0_13);
        aw2023_set_on_time(AW2023_LED1, AW2023_PAT_TIME_0_26);
        aw2023_set_on_time(AW2023_LED2, AW2023_PAT_TIME_0_38);
        /* 7.Set fall-time */
        aw2023_set_fall_time(AW2023_LED0, AW2023_PAT_TIME_1_04);
        aw2023_set_fall_time(AW2023_LED1, AW2023_PAT_TIME_2_10);
        aw2023_set_fall_time(AW2023_LED2, AW2023_PAT_TIME_3_10);
        /* 8.Set off-time */
        aw2023_set_off_time(AW2023_LED0, AW2023_PAT_TIME_0_13);
        aw2023_set_off_time(AW2023_LED1, AW2023_PAT_TIME_0_26);
        aw2023_set_off_time(AW2023_LED2, AW2023_PAT_TIME_0_38);
        /* 9.Set delay time of pattern startup */
        aw2023_set_delay_time(AW2023_LED0, AW2023_PAT_TIME_0_13);
        aw2023_set_delay_time(AW2023_LED1, AW2023_PAT_TIME_0_26);
        aw2023_set_delay_time(AW2023_LED2, AW2023_PAT_TIME_0_38);
        /* 10.Set breath repeat time */
        aw2023_set_repeat_time(AW2023_LED0, AW2023_BREATH_REPEAT_FOREVER);
        aw2023_set_repeat_time(AW2023_LED1, AW2023_BREATH_REPEAT_FOREVER);
        aw2023_set_repeat_time(AW2023_LED2, AW2023_BREATH_REPEAT_FOREVER);
        /* 11.Set operating mode: pattern mode*/
        aw2023_set_led_operating_mode(AW2023_LED0, AW2023_PATTERN_MODE);
        aw2023_set_led_operating_mode(AW2023_LED1, AW2023_PATTERN_MODE);
        aw2023_set_led_operating_mode(AW2023_LED2, AW2023_PATTERN_MODE);
        /* 12.All led enable */
        aw2023_led_enable_ctrl(AW2023_LED0_EN | AW2023_LED1_EN | AW2023_LED2_EN, AW_TRUE);
    }

    return ret;
}

/* aw2023 all led breath (pattern mode with sync) */
AW_BOOL aw2023_all_led_breath_with_sync(void)
{
    AW_BOOL ret = AW_FALSE;

    /* 1.Chip initialization */
    ret = aw2023_init();
    if (ret == AW_TRUE)
    {
        /* 2.Select pwm transition mode */
        aw2023_set_pwm_mode(AW2023_PWM_EXPONENTIAL);
        /* 3.Synchronous control mode enable */
        aw2023_set_sync_mode(AW_TRUE);
        /* 4.Set the LED port output current */
        aw2023_set_led_current(AW2023_LED0, 0x0f);
        aw2023_set_led_current(AW2023_LED1, 0x0f);
        aw2023_set_led_current(AW2023_LED2, 0x0f);
        /* 5.Set led pwm dimming level */
        aw2023_set_pwm_dimming(AW2023_LED0, 0xff);
        /* 6.Set breath timing parameter */
        aw2023_set_rise_time(AW2023_LED0, AW2023_PAT_TIME_1_04);
        aw2023_set_on_time(AW2023_LED0, AW2023_PAT_TIME_0_13);
        aw2023_set_fall_time(AW2023_LED0, AW2023_PAT_TIME_1_04);
        aw2023_set_off_time(AW2023_LED0, AW2023_PAT_TIME_0_13);
        aw2023_set_delay_time(AW2023_LED0, AW2023_PAT_TIME_0_13);
        aw2023_set_repeat_time(AW2023_LED0, AW2023_BREATH_REPEAT_FOREVER);
        /* 7.Set operating mode: pattern mode*/
        aw2023_set_led_operating_mode(AW2023_LED0, AW2023_PATTERN_MODE);
        /* 8.All led enable */
        aw2023_led_enable_ctrl(AW2023_LED0_EN | AW2023_LED1_EN | AW2023_LED2_EN, AW_TRUE);
    }

    return ret;
}

/* aw2023 manual mode */
AW_BOOL aw2023_all_led_manual_ctrl(void)
{
    AW_BOOL ret = AW_FALSE;

    /* 1.Chip initialization */
    ret = aw2023_init();
    if (ret == AW_TRUE)
    {
        /* 2.Select pwm transition mode */
        aw2023_set_pwm_mode(AW2023_PWM_EXPONENTIAL);
        /* 3.PWM fade-in enable */
        aw2023_fade_in_enable_ctrl(AW2023_LED0, AW_TRUE);
        aw2023_fade_in_enable_ctrl(AW2023_LED1, AW_TRUE);
        aw2023_fade_in_enable_ctrl(AW2023_LED2, AW_TRUE);
        /* 4.PWM fade-out enable */
        aw2023_fade_out_enable_ctrl(AW2023_LED0, AW_TRUE);
        aw2023_fade_out_enable_ctrl(AW2023_LED1, AW_TRUE);
        aw2023_fade_out_enable_ctrl(AW2023_LED2, AW_TRUE);
        /* 5.Set PWM fade-in time */
        aw2023_set_rise_time(AW2023_LED0, AW2023_PAT_TIME_0_51);
        aw2023_set_rise_time(AW2023_LED1, AW2023_PAT_TIME_0_77);
        aw2023_set_rise_time(AW2023_LED2, AW2023_PAT_TIME_1_04);
        /* 6.Set PWM fade-out time */
        aw2023_set_fall_time(AW2023_LED0, AW2023_PAT_TIME_0_51);
        aw2023_set_fall_time(AW2023_LED1, AW2023_PAT_TIME_0_77);
        aw2023_set_fall_time(AW2023_LED2, AW2023_PAT_TIME_1_04);
        /* 7.Set the LED port output current */
        aw2023_set_led_current(AW2023_LED0, 0x0f);
        aw2023_set_led_current(AW2023_LED1, 0x0f);
        aw2023_set_led_current(AW2023_LED2, 0x0f);
        /* 8.Select led operating mode: manual mode */
        aw2023_set_led_operating_mode(AW2023_LED0, AW2023_MANUAL_MODE);
        aw2023_set_led_operating_mode(AW2023_LED1, AW2023_MANUAL_MODE);
        aw2023_set_led_operating_mode(AW2023_LED2, AW2023_MANUAL_MODE);
        /* 9.All led enable */
        aw2023_led_enable_ctrl(AW2023_LED0_EN | AW2023_LED1_EN | AW2023_LED2_EN, AW_TRUE);
        /* 10.Set led pwm dimming level */
        aw2023_set_pwm_dimming(AW2023_LED0, 0xff);
        aw2023_set_pwm_dimming(AW2023_LED1, 0xff);
        aw2023_set_pwm_dimming(AW2023_LED2, 0xff);
        /* Delay 1000ms */
        delay_ms(100);
        /* 11.Set led pwm dimming level */
        aw2023_set_pwm_dimming(AW2023_LED0, 0x00);
        aw2023_set_pwm_dimming(AW2023_LED1, 0x00);
        aw2023_set_pwm_dimming(AW2023_LED2, 0x00);
    }

    return ret;
}

/* aw2023 led manual mode with sync */
AW_BOOL aw2023_all_led_manual_ctrl_with_sync(void)
{
    AW_BOOL ret = AW_FALSE;

    /* 1.Chip initialization */
    ret = aw2023_init();
    if (ret == AW_TRUE)
    {
        /* 2.Select pwm transition mode */
        aw2023_set_pwm_mode(AW2023_PWM_EXPONENTIAL);
        /* 3.Synchronous control mode enable */
        aw2023_set_sync_mode(AW_TRUE);
        /* 4.PWM fade-in and fade-out enable */
        aw2023_fade_in_enable_ctrl(AW2023_LED0, AW_TRUE);
        aw2023_fade_out_enable_ctrl(AW2023_LED0, AW_TRUE);
        /* 5.Set PWM fade-in and fade-out time */
        aw2023_set_rise_time(AW2023_LED0, AW2023_PAT_TIME_0_77);
        aw2023_set_fall_time(AW2023_LED0, AW2023_PAT_TIME_0_77);
        /* 6.Set the LED port output current */
        aw2023_set_led_current(AW2023_LED0, 0x0f);
        aw2023_set_led_current(AW2023_LED1, 0x0f);
        aw2023_set_led_current(AW2023_LED2, 0x0f);
        /* 7.Select led operating mode: manual mode */
        aw2023_set_led_operating_mode(AW2023_LED0, AW2023_MANUAL_MODE);
        /* 8.All led enable */
        aw2023_led_enable_ctrl(AW2023_LED0_EN | AW2023_LED1_EN | AW2023_LED2_EN, AW_TRUE);
        /* 9.Set led pwm dimming level */
        aw2023_set_pwm_dimming(AW2023_LED0, 0xff);
        /* Delay 1000ms */
        delay_ms(100);
        /* 10.Set led pwm dimming level */
        aw2023_set_pwm_dimming(AW2023_LED0, 0x00);
    }

    return ret;
}

AW_BOOL aw2023_play(void)
{
    AW_BOOL ret = AW_FALSE;

    /* all led full on */
    ret = aw2023_all_led_full_on();
    /* all led breath */
    ret = aw2023_all_led_breath();
    /* all led breath with sync */
    ret = aw2023_all_led_breath_with_sync();
    /* all led manual control */
    ret = aw2023_all_led_manual_ctrl();
    /* all led manual control with sync */
    ret = aw2023_all_led_manual_ctrl_with_sync();

    return ret;
}

/* Helper: setup AW2023 for manual control of all LEDs */
static AW_BOOL aw2023_setup_for_manual(void)
{
    AW_BOOL ret = aw2023_init();
    if (ret != AW_TRUE)
    {
        return AW_FALSE;
    }

    /* Set global config */
    aw2023_set_pwm_mode(AW2023_PWM_LINEAR); // Linear for instant response
    aw2023_set_pwm_freq(AW2023_PWM_FREQ_250HZ);

    /* Configure each LED for manual mode */
    for (AW_U8 i = 0; i < AW2023_LED_NUM; i++)
    {
        aw2023_set_led_current(i, 0x0F); // Max current
        aw2023_set_led_operating_mode(i, AW2023_MANUAL_MODE);
        aw2023_fade_in_enable_ctrl(i, AW_FALSE);  // No fade-in
        aw2023_fade_out_enable_ctrl(i, AW_FALSE); // No fade-out
        aw2023_set_pwm_dimming(i, 0x00);          // Start OFF
    }

    /* Enable all LEDs (but PWM=0 so they're off) */
    aw2023_led_enable_ctrl(AW2023_LED0_EN | AW2023_LED1_EN | AW2023_LED2_EN, AW_TRUE);

    return AW_TRUE;
}

/* Turn on only one LED, turn others off */
static void aw2023_set_active_led(AW_U8 led_index)
{
    // Turn all OFF first
    aw2023_set_pwm_dimming(AW2023_LED0, 0x00);
    aw2023_set_pwm_dimming(AW2023_LED1, 0x00);
    aw2023_set_pwm_dimming(AW2023_LED2, 0x00);

    // Turn target ON
    if (led_index < AW2023_LED_NUM)
    {
        aw2023_set_pwm_dimming(led_index, 0xFF);
    }
}

/* Rotate LEDs for N rounds */
AW_BOOL aw2023_led_rotate(AW_U32 rounds, AW_U32 delay_ms_per_led)
{
    if (rounds == 0 || delay_ms_per_led == 0)
    {
        return AW_FALSE;
    }

    AW_BOOL ret = aw2023_setup_for_manual();
    if (ret != AW_TRUE)
    {
        return AW_FALSE;
    }

    for (AW_U32 r = 0; r < rounds; r++)
    {
        for (AW_U8 i = 0; i < AW2023_LED_NUM; i++)
        {
            aw2023_set_active_led(i);
            delay_ms(delay_ms_per_led);
        }
    }

    // Turn all off at the end
    aw2023_set_active_led(AW2023_LED_NUM); // All off
    return AW_TRUE;
}

/* Infinite rotation (suitable for RTOS task) */
void aw2023_led_rotate_forever(AW_U32 delay_ms_per_led)
{
    if (delay_ms_per_led == 0)
    {
        delay_ms_per_led = 500;
    }

    aw2023_setup_for_manual();

    while (1)
    {
        for (AW_U8 i = 0; i < AW2023_LED_NUM; i++)
        {
            aw2023_set_active_led(i);
            delay_ms(delay_ms_per_led);
        }
    }
}