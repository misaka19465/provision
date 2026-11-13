# 可选 CW 播放配置扩展

本扩展用于设置 Morse 点位的 WPM、TONE、DASH 时长和 Character Space，沿用 v1 的帧、CRC、SEQ 与响应状态规则。上下位机同步使用完整五字节 CW 参数。基础配置流程不探测、不发送本扩展，不增加 `setup` 的必选参数。未设置时 UV-K5 使用 **8 WPM、700 Hz、DASH 3 dit、Character Space 3 dit**。

## 命令

| CMD | 命令 | 请求载荷 | 成功响应数据（不含状态字节） |
|---|---|---|---|
| `0x09` | GET_CW | 空 | `u8 wpm, u16 tone_hz, u8 dash_dits, u8 char_space_dits` |
| `0x0A` | SET_CW | `u8 wpm, u16 tone_hz, u8 dash_dits, u8 char_space_dits` | 与请求相同顺序的实际生效值 |

所有多字节字段均为小端。WPM 范围为 5–60，音调范围为 300–1200 Hz；DASH 和 Character Space 各为 **1–255 个整数 dit**，默认均为 3。Character Space 表示相邻两个字符（数字）之间的完整静音时长。GET_CW 返回当前生效值；没有有效 CW 记录时返回默认值。SET_CW 同时提交四项参数，完成持久化和读回校验后才更新当前值，成功数据回显实际生效值。相同记录不重复物理写入。

GET_CW 的请求长度必须为 0，SET_CW 必须为 5 字节，否则返回 `ERR_LEN`。成功响应数据固定为 5 字节。参数越界返回 `ERR_PARAM`。存储读取、写入或读回校验失败返回 `ERR_EEPROM`，本次请求不改变当前生效值；发生写入错误后，重启按实际有效记录恢复，记录无效则恢复全部默认值。

数显 `totp/` 设备对这两个命令返回 `ERR_UNSUPPORTED`，错误长度仍返回 `ERR_LEN`。上位机只在用户显式执行 CW 命令时发送扩展；错误或超时按该命令失败处理，普通 `setup`、写种子、读状态和校时均不依赖扩展支持。不增加设备类型参数或修改 PING 字段。

## 播放窗口

点长（1 dit）为 `floor(1200 / wpm)` 毫秒，划长为 `dash_dits` 个点长，字符间隔为 `char_space_dits` 个点长，符号内间隔为 1 个点长，完整帧间隔仍为 3 秒。最后一个字符后不追加 Character Space。修改 CW 参数前取消当前帧，下一帧使用新参数；每帧冻结码速、音调和两项 dit 时序。

UV-K5 写种子时使用当前 CW 参数校验最慢完整帧；SET_CW 则使用候选的完整 CW 参数校验已保存的有效种子。划长至少为 1 dit，因此全零数字串仍为最慢帧。若不能满足下式，返回 `ERR_PARAM`，不写入任何记录：

```text
units = (5 * dash_dits + 4) * digits + (digits - 1) * char_space_dits
period_s * 1000 >= 100 + units * floor(1200 / wpm) + 250
```

100 ms 为音频启动等待，250 ms 为窗口余量。种子和 CW 参数独立保存，WRITE_SLOT、READ_META、ERASE_SLOT 与 `config_crc16` 的既有格式不变。擦除或重新配置种子不会重置 CW 参数。EEPROM 只读取和写入当前版本 2 的 CW 记录；旧版 CW 记录失效，更新固件后需重新设置播放参数。上电不写入默认记录。旧版、损坏或读取失败的 CW 记录使用全部默认值，仍保留有效种子；播放调度继续逐帧检查窗口，无法容纳当帧时保持静音等待。若周期不再满足最慢完整帧要求，需缩短 CW 时序、提高 WPM 或增大周期。

## 命令行

```bash
python3 provision.py -p /dev/ttyUSB0 get-cw
python3 provision.py -p /dev/ttyUSB0 set-cw --wpm 12 --tone 900
python3 provision.py -p /dev/ttyUSB0 set-cw --dash 4 --character-space 7
python3 provision.py -p /dev/ttyUSB0 set-cw --wpm 8
python3 provision.py -p /dev/ttyUSB0 set-cw --tone 700
```

`--char-space` 是 `--character-space` 的别名。`set-cw` 至少指定一项；省略项先用 GET_CW 读取当前值再完整提交，不会用默认值覆盖已有设置。恢复原始参数可显式执行 `set-cw --wpm 8 --tone 700 --dash 3 --character-space 3`。

## 互操作向量

以下均为 SEQ=0，包含帧头和帧 CRC：

| 操作 | 线字节 |
|---|---|
| GET_CW 请求 | `aa 09 00 00 0d 52` |
| GET_CW 默认值响应 | `aa 89 00 06 00 08 bc 02 03 03 a7 73` |
| SET_CW，12 WPM / 900 Hz / DASH 4 dit / Character Space 7 dit | `aa 0a 00 05 0c 84 03 04 07 d2 9a` |
| SET_CW 成功响应 | `aa 8a 00 06 00 0c 84 03 04 07 ec 90` |
| 数显设备 GET_CW 不支持 | `aa 89 00 01 08 b6 18` |
