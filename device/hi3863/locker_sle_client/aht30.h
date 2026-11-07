#ifndef AHT30_H
#define AHT30_H

#include <stdint.h>   

uint32_t AHT30_Calibrate(void);
uint32_t AHT30_StartMeasure(void);
uint32_t AHT30_GetMeasureResult(float* temp, float* humi);


#endif  
