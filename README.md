# Radio Provision

一个供 `totp/` 与 `uvk5-morse/` 共用的 TOTP 串口配置项目。协议编解码、串口会话、配置命令和测试只维护在这里。

串口固定为 38400 8N1，两类设备使用相同的命令和配置流程，无需指定设备类型。

默认周期统一为 60 秒；`--period` 可显式指定 15–300 秒。UV-K5 固件会拒绝无法容纳完整 CW 帧的周期；数显设备支持 RTC 校准，UV-K5 对校准命令返回 `ERR_UNSUPPORTED`。工具直接报告设备返回的错误。已有 TOTP 设备需重新烧录采用 38400 的固件后再连接。

`setup` 通过 WRITE_SLOT 响应、READ_META 字段与配置 CRC、SET_TIME 响应和状态命令完成验证，不要求人工听码或对屏幕读数。

## 安装

需要 Python 3.10 或更新版本：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 使用

```bash
.venv/bin/python provision.py --port /dev/ttyUSB0 setup
.venv/bin/python provision.py --port /dev/ttyUSB0 setup --period 30
.venv/bin/python provision.py --port /dev/ttyUSB0 ping
.venv/bin/python provision.py --port /dev/ttyUSB0 set-calib -500
```

`--port` 放在子命令前。省略种子时工具会生成并在写入前打印随机种子；写入后不会再回显。`--no-retry` 可用于只尝试一次握手。

## 可选 CW 配置

UV-K5 的 WPM / TONE / DASH / Character Space 使用独立扩展命令，不要求配置；未配置时为 **8 WPM、700 Hz、DASH 3 dit、Character Space 3 dit**。普通 `setup` 不发送扩展，也不会覆盖已经保存的播放参数。数显 `totp/` 设备返回 `ERR_UNSUPPORTED`。

```bash
.venv/bin/python provision.py --port /dev/ttyUSB0 get-cw
.venv/bin/python provision.py --port /dev/ttyUSB0 set-cw --wpm 12 --tone 900
.venv/bin/python provision.py --port /dev/ttyUSB0 set-cw --dash 4 --character-space 7
.venv/bin/python provision.py --port /dev/ttyUSB0 set-cw --tone 700
```

WPM 支持 5–60，TONE 支持 300–1200 Hz；DASH 时长和 Character Space 均支持 1–255 个整数 dit，默认均为 3 dit，1 dit 为 `floor(1200 / WPM)` 毫秒。Character Space 是两个字符之间的完整静音间隔，`--char-space` 为别名。至少指定一项，未指定项保留当前值。配置在设备重启后保留；码速和时序不能导致已保存的 TOTP 周期无法容纳完整 CW 帧。配置工具和固件需同步更新，统一使用完整五字节参数；旧 EEPROM CW 记录失效并恢复全部默认值，需重新配置播放参数。协议字段、错误语义和测试向量见 [可选 CW 播放配置扩展](docs/cw-protocol.md)。

## 测试

```bash
python3 -m unittest discover -v
```
