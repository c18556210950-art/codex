# CarPilot 同步说明 · 2026-10-27

此文档对应本分支的 Windows 网页、服务器和 PC 实现，供 Mac 原生 CarPilot 逐项同步。Mac 用户已确认原生 App 可打开并识别地面，但此快照不宣称已验证原生导航、震动或实车自动驾驶。此分支只上传代码与离线测试，没有部署、刷机、控制电机或重启 PC 计算。所有配置与示例链接均使用占位符。充电宝微动功能已删除，勿恢复。

## 1. 配置、坐标与通道

私密配置从仓库外注入。示例见 `config.example.json`；真实密码、Key、设备链接及带密钥控制地址不进仓库。HTTP 基址为 `https://YOUR_DOMAIN/car/REPLACE_CONTROL_KEY/`，以下相对路径均接在此基址之后。

| 通道 | 用途 |
| --- | --- |
| HTTP JSON | 状态、手动驾驶、停止、地点搜索、路线规划 |
| WSS `camera` | JPEG 推流/观看；相机发布者可附带传感器数据 |
| WSS `vision` | 操作端、独立传感器发布端、可信 PC 感知端；导航与自动驾驶消息 |
| WSS `/car-link/REPLACE_DEVICE_KEY` | 已有 ESP 连接，不由原生控制 App 冒充 |

原生网络客户端可不发送 `Origin`；若发送，必须等于私密配置的 `publicOrigin`。HTTP POST 使用 `Content-Type: application/json`，JSON 请求体上限 1024 字节。错误为 `{"error":"说明"}`：400 参数/提供商错误，403 Origin 不符，409 控制冲突/手持测试锁定，503 车辆离线。不要把服务端 `computeKey` 嵌入可分发 App。

位置和内部路线统一 WGS84，字段 `{ "lat": 32.0, "lng": 118.0 }`；示例坐标仅为合成测试点。经度在高德的字符串参数中排在纬度之前。手机位置来自系统 GPS；网页 Leaflet/OSM 显示内部 WGS84 路线。高德搜索结果原始位置为 GCJ-02，服务端通过 6 次正向转换误差迭代返回 WGS84。规划时调用高德坐标转换接口把 WGS84 起终点转成 GCJ-02，再调用步行规划；返回的每个 polyline 点转换回 WGS84。原生端不要重复偏移坐标，尤其不要把已转回 WGS84 的结果直接当作 GCJ-02。

## 2. HTTP JSON 契约

### 查询状态与反馈

`GET api/status` 返回 ESP 状态与中转状态的合并对象（额外字段可扩展）：

```json
{"cloud_online":true,"connected":true,"motor_running":false,"motor_mode":"idle","motor_phase":"stopped","a_percent":0,"b_percent":0,"cloud_server_time":100000}
```

`GET api/ping?token=example` → `{"reply":"PONG","token":"example"}`。`GET api/navigation/provider` → `{"amap_enabled":true}`。

`GET api/feedback` 返回图像抖动反馈：

```json
{"valid":true,"moving":true,"intensity":0.4,"confidence":0.9,"frame_age_ms":125,"server_time":100000}
```

`intensity` 与 `confidence` 范围 0–1；感知结果须 ≤450 ms、相机 ≤700 ms 且置信度 ≥0.5 才 `valid`。`moving` 根据近期 ESP 软件状态判断，并不是轮速传感器。停车、无效反馈或失联应停止震动。PC 的 LK 光流与 RANSAC 全局运动估计用于过滤平稳平移，只控制反馈，不参与油门。网页 API 能力与实际手柄震动效果分开显示；iPad Safari 不支持的震动接口不能伪称可用，原生端需自行验证 GameController 支持。

### 手动驾驶与停止

`POST api/drive`：

```json
{"client":"ios-session-example","seq":1,"x":0.2,"y":1,"speed":80,"invert_a":false,"invert_b":false}
```

`x,y` 为有限数 [-1,1]；`speed` 为整数 0–100；反向标志为可选布尔值。成功返回与 `api/status` 同形的状态快照，不等于物理轮子已经执行。手动命令接管并解除自动驾驶；其他手动控制者占用时可能 409。

`POST api/motor/stop` 可发 `{}`，无需序号即可紧急停止、退出手持测试并解除自动驾驶；若附带 `client`，则必须同时提供有效递增 `seq`。原生端应优先使用无序号 `{}` 急停，避免旧序号造成请求报错。`x=0,y=0` 也能停止本次手动输出，但不替代明确的急停入口。

旧 `api/motor/a|b|both|stagger` 服务端兼容接口尚在，单项测试网页已删除；不要在原生 UI 重新引入，不要调用这些接口做同步验证。

### 搜索地名 → 选择 → 规划 → 显示路径

`POST api/navigation/search` 请求 `{"keyword":"公园","city":"测试市"}`，`city` 可省略。关键字 1–80 字、城市 ≤40 字；搜索间隔至少 1 秒。未指定城市时，可根据新鲜手机位置反向地理编码选择城市；失败可回退全国检索。

```json
{"keyword":"公园","city":"测试市","source":"高德地点搜索","items":[{"id":"example-poi","name":"公园东门","address":"示例路1号","city":"测试市","district":"测试区","point":{"lat":32.001,"lng":118.001},"uses_entrance":true,"distance_m":150}]}
```

有合理入口坐标时优先入口（距 POI 中心 <500 米）；无坐标结果过滤。原生端显示名称、地址、城市/区和距离，用户选中后使用返回的 `point`，不要仅取第一条结果或再次猜测坐标。

`POST api/navigation/plan`：

```json
{"origin":{"lat":32,"lng":118},"destination":{"lat":32.001,"lng":118.001},"radius":20}
```

`origin` 可省略，此时服务器要求手机上传来源最近收到 ≤10 秒、GPS 测量 ≤15 秒；这属于“规划起点”要求，与行驶测试的 30 秒有效窗口不同。显式提供 `origin` 不代表可以伪造新定位。`radius` 必须为整数 5–30；起终点距离与提供商步行路线距离均限 2 公里；规划间隔至少 3 秒。

响应为 `{plan_id, route, origin, destination}`；`route` 主要字段如下（单位米、秒、度）：

```json
{"points":[{"lat":32.001,"lng":118.001}],"path":[{"lat":32,"lng":118},{"lat":32,"lng":118.001},{"lat":32.001,"lng":118.001}],"commands":[{"start_m":0,"end_m":94,"distance_m":94,"bearing_deg":90,"turn_deg":0,"action":"forward","text":"前进约94米"}],"radius":20,"calibration_point":{"lat":32,"lng":118.001},"provider":"amap","blocked":false,"meta":{"distance_m":205,"geometry_m":205,"duration_s":160,"start_gap_m":0,"end_gap_m":0,"instructions":[],"warnings":[],"source":"高德步行路线","coordinate_system":"WGS84（已从GCJ-02转换）"}}
```

示例 `commands` 省略了后续段；实际返回完整数组。相邻段方向变化 <25° 或上一段 <5 米时合并；其余按相对方位变化产生 forward/left/right。导航仍根据当前位置投影到路线和朝向持续修正，不能把这些文字直接变成固定时间开环行驶。绘制 `route.path` 全部点并缩放地图；不要只连起终点。

HTTP 规划仅创建预览，**不直接驱动小车**。WSS `vision` 发送 `{"type":"nav_import","plan_id":"REPLACE_PLAN_ID"}` 才载入该服务进程保存的路线。预览最多保留 4 个、10 分钟有效，服务重启后失效。网页选中目的地后规划成功会自动载入；原生端需明确执行同等载入步骤。

## 3. 导航、传感器与自动驾驶 WebSocket

操作端连接 `vision` 后接收 `vision_state`。服务定期推送，主要结构为：

```json
{"type":"vision_state","enabled":false,"controller":"","reason":"未启动","pc_online":true,"model_ready":true,"camera_online":true,"result_age_ms":125,"server_time":100000,"navigation":{"revision":1,"points":[],"radius":20,"gps_test":true,"handheld":false,"active":false,"status":null},"sensors":null,"result":null,"diagnostics":{"mode":"explore","searching":false,"paused":false,"recovery_required_frames":1,"recovery_required_ms":0,"explore_blockers":[],"navigation_blockers":[],"last_command":null,"command_history":[],"feedback":{}},"haptics":{"valid":false,"moving":false,"intensity":0}}
```

实际非空 `result` 包含视觉 scores/clear/steering/reason 等；`navigation.status` 包含 ready/stage/reason/revision/index/total/distance_m/heading_error/accuracy_m/heading_accuracy_deg/heading_accuracy_ok/location_age_ms/location_timeout_ms 等。字段未计算时可能缺失。`diagnostics.last_command` 是下发记录，`feedback` 是 ESP 软件报告，二者均不等于机械运动确认。

原生传感器端先发送 `{"type":"sensor_claim"}`，收到 `{"type":"sensors_claimed"}` 后持续上传：

```json
{"type":"phone_sensors","latitude":32,"longitude":118,"accuracy":35,"location_at":100000,"location_age_ms":200,"camera_heading":90,"camera_heading_absolute":true,"heading_accuracy":40,"heading_age_ms":80,"gps_callbacks":5,"gps_fixes":3,"gps_error":"","motion":{"acceleration":{"x":0,"y":0,"z":0},"rotation":{"alpha":0,"beta":0,"gamma":0}}}
```

`location_at` 是原始测量时间戳，用作测量 ID；`location_age_ms` 由采集端当前时刻减测量时刻得出，服务器据此构造服务端 `location_at`。重发与心跳不能将旧测量年龄归零。`camera_heading` 为镜头水平指向的绝对北向角 [0,360)，不是直接把手机平放时的罗盘数值照搬；网页通过姿态投影并用 Safari `webkitCompassHeading` 锚定。原生端需用自身姿态得到相同含义。朝向年龄同理。GPS 精度单位米，朝向精度单位度；`heading_accuracy` 缺失或负数视为未知。浏览器上传的是罗盘朝向误差，没有单独的陀螺仪精度数值。

采集源切换会解除自动驾驶、清校准并增加 revision；不要为读取状态而发送 `sensor_claim` 抢占车上手机。独立源存在时，相机通道附带的 sensors 不覆盖它。来源断连时导航解除。网页 `sensors` 展示要求最近上传 <10 秒，故应持续上传，同时保留原始定位年龄。

| 客户端 JSON 消息 | 含义 |
| --- | --- |
| `{"type":"nav_gps_mode","test":true}` | 默认测试模式；改模式会停止，需要重启导航 |
| `{"type":"nav_set","points":[{"lat":32.001,"lng":118}],"radius":20}` | 手动航点，最多20点，单段≤1公里、总长≤2公里、相邻点距离>2倍半径 |
| `{"type":"nav_import","plan_id":"REPLACE_PLAN_ID"}` | 载入规划路线并停止当前驾驶 |
| `{"type":"nav_test_start","client":"ios-session-example"}` | 手持测试，不需相机，所有电机入口锁定 |
| `{"type":"nav_test_lease"}` / `{"type":"nav_test_stop"}` | 同一 socket 保持/结束手持测试，租约1800ms |
| `{"type":"arm","client":"ios-session-example","mode":"explore","speed":80,"invert_a":false,"invert_b":false}` | 启用无需路线的自主找路；mode 改为 navigate 启用小车导航 |
| `{"type":"lease","client":"ios-session-example"}` | 自动驾驶操作端持续心跳，须为原 arm socket |
| `{"type":"stop"}` | 解除自动驾驶；急停还可调用 HTTP stop |

导航修改成功回 `{"type":"nav_saved","action":"nav_import"}` 等；错误回 `{"type":"vision_error","error":"说明"}`，随后检查状态，不要误判成功。切换手持/实车模式要等待匹配新 revision 的计算结果，不能沿用上一模式的 ready。

可信 PC 使用 `X-Vision-Key: REPLACE_COMPUTE_KEY` 连接 vision，发送 ready、heartbeat、nav_status、result。PC 收到 `{type:"navigation",server_time,navigation,sensors}` 与 `{type:"frame",id,at,jpeg}`，结果 frame_id 必须对应服务器已发送的帧；结果还须带有限 steering/throttle、3项 scores、boolean clear、reason 和 overlay。原生本地计算不能仅沿用操作端连接冒充可信 PC；本分支没有增加可分发的手机感知端认证协议。

## 4. 时效、GPS 测试模式与限制

默认 `gps_test=true`：GPS 误差数值大不单独停车；定位有效窗口 **30 秒**（≤30秒有效）。朝向精度 >25°、未知或负数也不单独停车，UI 显示 `约 ±N° / 良好 / 偏低 / 未提供有效精度`。仍必须存在有限且新鲜（≤1500ms）的有效朝向和真实 GPS；不把“精度未知”当作“数据存在”。缺失/过期数据、明显偏离路线、异常跳变、断连仍阻止驾驶。

严格模式检查 GPS 精度 ≤min(10米, 到达半径/2)、定位 ≤4秒；严格手持预览则≤30米、≤15秒。严格模式均要求朝向精度 0–25°。旧手动校准接口仍要求 4 秒内 GPS、≤10 米和良好罗盘；默认使用绝对朝向时无需此步骤。

路线投影只在历史进度附近搜索，不因 GPS 误差大任意跳到远处路线。测试模式用于路线搜索窗口/离线判断的 accuracy 上限10米；偏离路线 >max(8米, cappedAccuracy×2) 仍暂停。异常跳变阈值 max(8米, 前后定位误差和 + 时间差×3m/s)。测试到达按距离≤半径、严格按距离+误差≤半径，均需连续 **3个不同原始定位ID**；U形路线还检查沿路剩余距离，旧定位重发不能累加。

高德步行路线包含已标注人行横道/斑马线会 `blocked=true`，不开放自动过街；台阶、扶梯、电梯、轮渡、天桥等特殊类型也阻止实车执行（walk_type 集合见服务器源码）。起点或终点离道路吸附点 >20米同样阻止，不创建虚构直线接驳。手持测试可预览这些路线并显示提示，实车导航不能绕过；没有接入实时交通灯数据或实现红绿灯识别放行。

## 5. 方向、功率与 seq/租约兼容

底层 HTTP 与 ESP `drive` 协议未改变，原生手动客户端仍可沿用：`a=y+x; b=y-x; scale=max(|a|,|b|,1)`，每轮输出 `int(a*speed/scale)`、`int(b*speed/scale)`，最后应用 invert 标志。

手动 RT 对应 y>0、LT 对应 y<0、左摇杆控制 x。当前车上镜头**朝车尾**，自动行驶沿镜头方向倒行，因此自主/导航直行 `y=-1`；视觉左/右 steering=-0.85/+0.85，最终 `x=-steering*0.55`。这是当前实体接线校正后的约定，**不要因为 y 反向再反一次 x**。导航原地对齐或旋转找路时 `y=0,x=-sign(steering)`（找路首轮若有上次转向优先沿用，否则比较两侧评分，平手默认右侧）。镜头改朝车头时必须显式切换整套坐标约定并实测，不能单改标签。

自动固定所选 PWM 功率，默认80%、最高100%（arm 接口允许15–100）；感知 throttle 只判断是否可行，不用于降低正常输出。差速转向时内侧轮功率会减少，“固定功率”不等于恒定车速。手柄油门仍按输入量控制。人工按住大胆测试固定80%、每轮最多8秒，松开/心跳>350ms停止，与自主找路区分。

`client` 每次启动生成新会话字符串1–32字符，`seq` 为严格递增整数 1..999999999（上界不含10亿）；服务器与 ESP 都拒绝重复/倒退序号。服务器记忆32个控制者、ESP记忆8个；不要依赖表满清空规避校验。中转自动驾驶使用进程随机 client，解决中转重启后 ESP 仍记住旧序号的问题。

建议手动命令每200ms更新：中转报文 ttl=700ms，ESP 到达时检查年龄≤min(ttl,700ms)，ESP 本地700ms无新 drive 看门狗停车；中转手动800ms无命令也停车。自动循环150ms、普通自动 drive ttl500ms、找路旋转ttl350ms；操作端 lease>1100ms解除，PC心跳>1500ms解除；相机缺失解除，画面年龄>900ms或相机更新>1000ms则暂停。旧协议保持兼容，但不能取消租约、缓存驱动命令离线补发或把 TTL 当作可无心跳运行的授权。

## 6. 地面判定、一帧恢复、旋转找路

SegFormer B0 ADE20K 类别 floor/road/sidewalk/path 概率相加，概率≥0.62作为地面；3×3腐蚀。只保留与近处种子区连接的主区域，种子 x=36%..64%、y=85%..98%，主区域种子占比需>12%。坐标以画面左上为原点。

- 近处橙框：x=34%..66%、y=85%..98%，地面占比阈值0.82。
- 三个近通路梯形：底边x=34%..66%、y=98%；上边中心x=28%/50%/72%，各±10%、y=62%。至少一个通路占比≥0.88；中间达标且落后最佳<0.03时优先直行。
- 远处判断带 y=35%..58%，三个中心相同；中间远评分<0.92时，选择近处已达标且远处≥0.88、比中间高≥0.08的侧向通路提前转向。
- 清晰度Laplacian方差≥18；亮度低本身不否决已确认通路。图像比例不是厘米距离，分割没有提供可靠深度/悬崖判断。

临时暂停后保持 autoEnabled，**一帧有效通路立即恢复，0ms等待**；不需要旧版4帧/800ms。手动停止、急停、断连等解除后必须重新 arm，不自动重启。

自主找路若仍确认近地面、仅远通路短波动，可最多0.5秒保留上次转向与功率；要求近比≥0.96、亮度≥40、清晰度≥25、最佳分≥0.86、帧≤350ms，不延长该窗口。

之后遇到无通路，**仅 explore** 开始原地旋转找路：转0.6秒、停0.3秒观察，总尝试窗口6秒；旋转继续使用所选功率。识别一帧可走即恢复直行并重置搜索窗口。6秒耗尽则暂停，后续坏帧不会重新开启无限搜索；有效通路恢复后下一次受阻才可新开一轮。旧帧不触发旋转，画面过期停车；导航模式不套用此搜索替代 GPS/路线暂停。UI `diagnostics.searching` 与 reason 区分旋转搜索和正常暂停。

## 7. 验证与合并范围

`tests/` 的参考测试直接读取本仓库源码，使用模拟坐标、虚拟时钟、模拟高德响应或浏览器 DOM，不接生产车辆。覆盖地名搜索/选中/路线展示、坐标转换、限制路段、固定输出与方向、单帧恢复、6秒搜索上限、过期画面停车、GPS30秒边界与误差绕过、朝向精度显示字段等。参考固件的旧嵌入控制页已换成只读说明，避免重新带入已删除的保活代码。

此同步分支采用无父提交的脱敏快照，避免继承旧固件参考中的硬编码测试AP密码；没有重写 main 或任何 ios 分支。Mac 可比较目录后选择性移植，不应整树覆盖原生 App。图标和原生工程仍由 Mac 工作副本管理。
