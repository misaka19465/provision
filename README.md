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

## 测试

```bash
python3 -m unittest discover -v
```
