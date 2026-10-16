---
name: calibration
when: 需要对可编程电源进行输出校准时
tools: [hardware.read_state, hardware.actuate]
---
1. 读取 instr_power 当前温度，确认低于 60 度
2. 设置电压到 5.0V 并等待输出稳定
3. 记录基线测量值后再执行校准
