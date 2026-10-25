# iPhone 原生方案（待开发）

目标：iPhone 摄像头、定位、陀螺仪和加速度计作为数据来源，在手机本地计算，再向 ESP32 小车发送电机命令。保留网页遥控、远程观看与手动接管。

先在 Mac 检查 Xcode、可用 iOS SDK、开发签名和 iPhone 真机连接，再创建原生项目。不得把真实配置、签名证书、设备密钥或 API Key 放入 Git。

现有实现与协议见 `../docs/ios-handoff.md`。
