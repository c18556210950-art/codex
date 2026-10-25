# iPhone 小车项目

这是现有 ESP32-S3 小车、网页遥控、手机视频、PC 视觉计算及导航的去凭据代码快照，用于在 Windows 和 Mac 间协作，并继续开发 iPhone 原生本地计算方案。

## 项目目录

- `src/server/`：HTTPS 反向代理后方的 Node.js 控制中转服务，以及网页、手机 JPEG 推流、导航地图和高德路线规划。
- `src/pc/`：SegFormer 图像分割、视觉通路选择、手机 GPS/朝向导航和 PC 推理连接程序。
- `src/firmware/`：现有 ESP32-S3 MicroPython 固件参考。所有已知凭据已替换；不要将这个脱敏文件直接刷到正在使用的小车上。
- `ios/`：iPhone 原生开发位置；原生 App 尚未实现。
- `docs/`：功能说明及后续开发交接。

当前已部署的工作副本位于本机 `work/` 和 `outputs/`，不属于 Git 跟踪范围。整理 Git 项目没有更换生产服务的运行位置。

## 在另一台电脑保存同一项目

1. 把这个仓库推送到 GitHub 或 Gitee 的私有仓库。
2. Mac 从同一个远程地址 `git clone`。项目名称或文件夹名称不要求相同，远程仓库须相同。
3. Windows 和 Mac 的 Codex 分别添加该仓库的根目录为项目。
4. 需要跨主机移交会话时，再使用会话运行位置中的移交功能。

服务器密码、Wi-Fi 密码、控制链接中的访问密钥、高德 Key、设备密钥、计算端密钥不提交到 Git。Mac 如需实际连接服务，应单独提供配置文件。

## 参考服务的配置

复制 `config.example.json` 为 `config.private.json`，填写私密配置；后者已被 Git 忽略。文件中的控制密钥与设备/计算端密钥应由部署者配置，不应从示例值直接部署。

Node.js 22 或更新版本：

```sh
npm install
export CAR_CONFIG="$PWD/config.private.json"
npm run server
```

服务默认只监听 `127.0.0.1:18765`，公网 HTTPS 和反向代理需要在部署环境配置。不要在 Mac 上另启参考服务来替换现有公网服务，除非明确计划迁移。

PC 参考推理程序需要另行安装 Python 依赖和模型：

```sh
python -m pip install -r requirements.txt
export CAR_CONFIG="$PWD/config.private.json"
python src/pc/pc_vision_worker.py
```

当前 PC 版本自动选择 CUDA 或 CPU；这不代表 iPhone 原生计算已经完成。Mac/iPhone 方案需要单独实现 Core ML、ARKit、相机和运动数据接入。

## 验证

```sh
python tests/test_navigation_reference.py
```

该检查使用虚拟位置，不驱动实车。当前视觉及导航为低速测试原型，真实户外避障和导航效果尚需实车验证。
