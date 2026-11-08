# 可选 CW 播放配置扩展

本扩展用于设置 Morse 点位的 WPM 和 TONE，沿用 v1 的帧、CRC、SEQ 与响应状态规则。基础配置流程不探测、不发送本扩展，不增加 `setup` 的必选参数。未设置时 UV-K5 使用原有默认值 **8 WPM、700 Hz**。

## 命令

| CMD | 命令 | 请求载荷 | 成功响应数据（不含状态字节） |
|---|---|---|---|
| `0x09` | GET_CW | 空 | `u8 wpm, u16 tone_hz` |
| `0x0A` | SET_CW | `u8 wpm, u16 tone_hz` | `u8 applied_wpm, u16 applied_tone_hz` |

所有多字节字段均为小端。WPM 范围为 5–60，音调范围为 300–1200 Hz。GET_CW 返回当前生效值；没有有效 CW 记录时返回默认值。SET_CW 同时提交两项参数，完成持久化和读回校验后才更新当前值，成功数据回显实际生效值。相同记录不重复物理写入。

GET_CW 的请求长度必须为 0，SET_CW 必须为 3，否则返回 `ERR_LEN`。参数越界返回 `ERR_PARAM`。存储读取、写入或读回校验失败返回 `ERR_EEPROM`，本次请求不改变当前生效值；发生写入错误后，重启按实际有效记录恢复，记录无效则恢复默认值。

数显 `totp/` 设备对这两个命令返回 `ERR_UNSUPPORTED`，错误长度仍返回 `ERR_LEN`。旧固件可能返回 `ERR_UNKNOWN_CMD` 或不响应。上位机只在用户显式执行 CW 命令时发送扩展；错误或超时按该命令失败处理，普通 `setup`、写种子、读状态和校时均不依赖扩展支持。不增加设备类型参数或修改 PING 字段。

## 播放窗口

点长为 `floor(1200 / wpm)` 毫秒，划长和数字间隔为 3 个点长，符号内间隔为 1 个点长，完整帧间隔仍为 3 秒。修改 CW 参数前取消当前帧，下一帧使用新参数。

UV-K5 写种子时使用当前 WPM 校验最慢完整帧；SET_CW 则使用候选 WPM 校验已保存的有效种子。若不能满足下式，返回 `ERR_PARAM`，不写入任何记录：

```text
period_s * 1000 >= (22 * digits - 3) * floor(1200 / wpm) + 250
```

种子和 CW 参数独立保存，WRITE_SLOT、READ_META、ERASE_SLOT 与 `config_crc16` 的既有格式不变。擦除或重新配置种子不会重置 WPM / TONE。未配置 CW 时不会自动写入默认记录。CW 记录损坏或读取失败时使用默认值，仍保留有效种子；播放调度继续逐帧检查窗口，无法容纳当帧时保持静音等待。若周期不再满足最慢完整帧要求，需设置兼容的 WPM 或增大周期。

## 命令行

```bash
python3 provision.py -p /dev/ttyUSB0 get-cw
python3 provision.py -p /dev/ttyUSB0 set-cw --wpm 12 --tone 900
python3 provision.py -p /dev/ttyUSB0 set-cw --wpm 8
python3 provision.py -p /dev/ttyUSB0 set-cw --tone 700
```

`set-cw` 至少指定一项；省略项先用 GET_CW 读取当前值再完整提交，不会用默认值覆盖已有设置。恢复原始参数可显式执行 `set-cw --wpm 8 --tone 700`。

## 互操作向量

以下均为 SEQ=0，包含帧头和帧 CRC：

| 操作 | 线字节 |
|---|---|
| GET_CW 请求 | `aa 09 00 00 0d 52` |
| GET_CW 默认值响应 | `aa 89 00 04 00 08 bc 02 7e 2a` |
| SET_CW，12 WPM / 900 Hz | `aa 0a 00 03 0c 84 03 90 81` |
| SET_CW 成功响应 | `aa 8a 00 04 00 0c 84 03 21 b2` |
| 数显设备 GET_CW 不支持 | `aa 89 00 01 08 b6 18` |
