# iPhone 原生开发交接

## 已有功能

- ESP32-S3 MicroPython 双电机控制，驱动为 TB6612，电机供电5V。
- 引脚：STBY 7、PWMA 4、AIN1 5、AIN2 6、BIN1 15、BIN2 16、PWMB 17。
- 公网中转服务器、浏览器手柄和前后台/断线停车。
- 手机后置相机经 WebSocket 发送 JPEG，其他客户端观看。
- 浏览器授权手机位置与运动数据，PC 使用 NVIDIA SegFormer B0 / ADE20K 分割地面。
- 手动航点导航与高德自动步行路线规划；高德 Key 保存在服务器端。
- 充电宝微动保活已删除，不重新引入。

## 当前目标

用户希望开发 iPhone 本地计算方案，设备为不带壳的 iPhone Air，当前网页配置为摄像头朝车尾，沿镜头方向倒行。数据来源均来自 iPhone，可使用 GPS、运动传感器及 ARKit。Mac 端用户已打开 CarPilot App 并确认地面识别；本仓库 Windows 快照不包含该端尚未推送的原生实现。

## 连接协议参考

服务路径根为 `/car/<controlKey>/`。真实域名与控制密钥需要通过私密配置提供。

- `GET api/status`：车辆在线状态。
- `POST api/drive`：`client`、递增 `seq`、`x`、`y`、`speed`，可选电机反向设置。发送端需要持续刷新命令；不要取消指令有效期。
- `POST api/motor/stop`：停止并接管自动驾驶。
- WebSocket `camera`：文本 `publish` 注册手机来源；每帧先发 `frame_meta`，再发 JPEG 二进制；`sensors` 上传位置和运动信息。新来源会替换已有相机，不用测试推流干扰用户实况。
- WebSocket `vision`：现有浏览器操作端与 PC 感知端连接，后者使用服务器端配置的专用计算密钥。

具体字段和服务器约束以 `src/server/server.js` 与 `src/server/controller.html` 为准。原生本地计算不能简单把 PC 专用密钥硬编码进可分发 App，需要设计相应的手机感知端接入。

## 开发顺序建议

1. 检查 Mac 的 Xcode、iOS SDK、签名和 iPhone 真机；先只读检查。
2. 实现原生相机预览、运动/定位及 ARKit 姿态，展示数据质量。
3. 选定并验证 Core ML 模型，测量真实手机上的延迟、发热和准确性。
4. 先显示本地视觉结果，再连接短时低速控制与手动急停。
5. 接入导航与本地姿态，户外实车验证。

二维地面分割不等于可靠距离测量或完整避障，不能保证玻璃、台阶、细小障碍检测。原网页模式的模拟验证不能替代 iPhone 真机验证。

最新协议与行为差异见 [ios-sync-latest.md](ios-sync-latest.md)。
