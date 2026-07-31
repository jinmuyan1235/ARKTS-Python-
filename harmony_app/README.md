# Molecule Vision 鸿蒙移动端

该工程通过局域网 HTTP 调用 `D:\computer_vision_arkts` 中的 Python 视觉服务。鸿蒙端提供“识别、批量、SMILES、历史、设置”五栏界面；DECIMER/MolScribe 推理、SQLite 历史和报告文件都保留在电脑端。

## 1. 启动电脑端服务

PowerShell 的执行策略可能阻止 `.ps1`。下面的命令只对这一次启动绕过策略，不修改系统全局设置：

```powershell
cd D:\computer_vision_arkts
powershell -ExecutionPolicy Bypass -File .\start_harmony_api.ps1 `
  -PythonExe E:\Anaconda\envs\molecule-vision\python.exe `
  -ApiKey "请替换为你自己的本地密钥"
```

脚本默认使用 `production + decimer`，监听 `0.0.0.0:8000`。第一次加载 DECIMER 可能需要几十秒。只检查界面和接口时可使用快速 demo：

```powershell
cd D:\computer_vision_arkts
powershell -ExecutionPolicy Bypass -File .\start_harmony_api.ps1 `
  -PythonExe E:\Anaconda\envs\molecule-vision\python.exe `
  -ApiKey "请替换为你自己的本地密钥" `
  -AppMode demo `
  -Backend demo
```

`-ApiKey` 为必填项。服务端将其写入当前进程的 `HARMONY_API_KEY`，所有 `/api/v1/*` 请求都必须携带同一密钥。不要把真实密钥提交到代码仓库。

电脑端可用下面的命令检测服务：

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/v1/health `
  -Headers @{ "X-API-Key" = "与启动命令相同的密钥" }
```

## 2. 在 DevEco 模拟器运行

1. 用 DevEco Studio 打开 `D:\arkts_project`。
2. 启动手机模拟器并运行 `entry`。
3. 首次打开默认选择“普通用户”，服务地址保持为空，并显示连接引导。在登录卡片下方填写电脑服务地址（例如 `http://192.168.5.13:8000`）和启动命令中的 API 密钥；电脑 IP 变化时用 `ipconfig` 查看并替换。
4. 点击“注册组员”，填写显示名称、组内角色、用户名和至少 8 位密码。
5. 注册成功后自动进入工作台，以后可以直接登录。
6. 每名组员注册自己的账号，并在“设置”页点击“更换头像”；头像和角色会显示在项目组成员区域。
7. 模拟器图库或文件为空时，可直接使用随 HAP 打包的微型测试集：单图页可点选任意缩略图，批量页可逐项勾选或点击“全选内置”，文档页点击“内置 PDF”。

密码不会保存在鸿蒙端，电脑端只保存 PBKDF2 加盐哈希。API 密钥和登录令牌保存在 HarmonyOS Asset Store；Preferences 只保存服务地址、活动任务 ID 和界面状态。

模拟器中的 `127.0.0.1` 指向模拟器自身，不能用来连接电脑。电脑和模拟器网络必须互通；Windows 防火墙提示出现时，应允许 Python 在专用网络通信。

## 3. 移动端功能

- 识别：从内置微型测试集自由点选任意图片，也可从系统选择器拍照或重新选择；完整的应用内图片编辑器支持拖动/四角缩放裁剪框、自由与常用比例裁剪、左右旋转、水平/垂直翻转、重置、取消和应用，不会重新打开相册，并显示后台排队/推理状态和失败提示。
- 批量识别：逐项勾选或追加 8 张内置测试图，也可从图库多选最多 30 张；网格使用完整图像适配，可预览、逐张删除、勾选后批量移除并查看剩余容量，同时显示总进度、分类统计和逐项结果。
- 文档区域审核：识别框支持整框拖动和右下角缩放，扩大后的透明触控区不会遮住内容；切换区域、筛选或返回时自动保存编辑，区域识别期间锁定当前区域。
- 后台恢复：单图、批量、文档和区域识别任务标识保存在 HarmonyOS Preferences；回到应用或重启后继续查询。临时断网和 401 不会删除任务，只有服务端确认 404 时才清理失效标识。
- SMILES：输入单条 SMILES，电脑端校验、计算性质并写入历史。
- 历史：共享创建者标注、“全组 / 我的 / 组员”筛选、搜索、状态筛选、仅收藏、分页、详情、收藏和仅删除 SQLite 索引。
- 结果详情：上传原图与重绘结构对照、完整化学身份、图像质量、描述符、逐项 Lipinski、模型轨迹、告警和 ADMET 状态；专业信息按模块展开。
- 人工复核：支持直接确认、修正 SMILES 后确认、标记无法确认和撤销确认，并记录操作组员、时间、原因与备注；非法输入不会覆盖原报告。
- 结果分享：可复制 SMILES、生成不包含上传原图和敏感凭据的 PNG 品牌卡片，并通过系统分享面板发送。
- 导出中心：从单条详情或已结束的批量任务进入，保存或分享 CSV、JSON、PDF、SMI、MOL、SDF 和 ZIP；下载使用短时签名链接，正式结构格式仅包含人工确认结果。
- 模型运行状态：设置页展示电脑端真实检测到的模型名称、版本、设备、权重、Warm-up、任务队列和失败原因；演示后端与跳过检查会明确标注，不由移动端推断。
- 反馈数据审核：在设置页进入 `DatasetReviewView`，查看原图、预测 SMILES、人工修正、来源许可和审核记录，支持核验、退回、拒绝、标记许可不清以及导出已核验训练 Manifest。
- 设置：按“账号资料 / 账号安全 / 服务连接 / 开发工具”进入二级页面；所有账号都可配置、检测和保存电脑端服务连接，开发工具仅对开发者账号展示。
- 登录注册：本地账户、7 天登录会话、退出登录和启动时自动恢复。
- 组员资料：每个账号拥有独立姓名、角色和头像，设置页展示项目组成员。
- 现代实验室主题：浅色/深色自动跟随系统，使用分子网络背景、语义化状态色、统一实验室卡片和安全的结构图白色基底。
- 响应式界面：适配 320–480vp 手机宽度和系统字体放大；小屏结果性质卡片自动切换为单列。
- 统一状态体验：样例、历史、成员和结果页分别展示加载骨架、空状态、可读错误与重试入口。

历史和报告不在手机端重复存储。删除历史索引后，电脑端 `data\runs\<analysisId>` 中的报告和图片仍然保留。

## 4. 命令行构建

登录后可从“识别”页的“化学文档识别”卡片进入独立工作台。上传 PDF、PNG/JPG 或 ZIP
后先执行区域检测，再在区域审核页直接拖动识别框、拖动右下角缩放并选择区域类型。
只有手动确认的分子区域可以提交结构识别；反应流程区域仅做分类，不会在此阶段解析。

```powershell
cd D:\arkts_project
$env:DEVECO_SDK_HOME='E:\DevEco Studio\sdk'
$env:JAVA_HOME='E:\DevEco Studio\jbr'
& 'E:\DevEco Studio\tools\node\node.exe' `
  'E:\DevEco Studio\tools\hvigor\bin\hvigorw.js' `
  assembleHap --mode module -p product=default -p module=entry@default -p buildMode=debug --no-daemon
```

运行真实 ArkTS 单元测试：

```powershell
& 'E:\DevEco Studio\tools\node\node.exe' `
  'E:\DevEco Studio\tools\hvigor\bin\hvigorw.js' `
  test --mode module -p product=default -p module=entry@default -p buildMode=debug --no-daemon
```

构建产物：

```text
D:\arkts_project\entry\build\default\outputs\default\entry-default-unsigned.hap
```

未配置签名时可以完成 ArkTS 编译和 HAP 打包，但真机安装前需要在 DevEco Studio 中配置签名；IDE 自带模拟器可直接从 DevEco Studio 运行。

仓库根目录的 `.github/workflows/harmony.yml` 会在配置好的 Windows HarmonyOS 自托管 Runner 上执行 ArkTS 测试和 HAP 构建。Runner 需要设置
`HARMONY_CI_ENABLED=true`，并提供 `DEVECO_SDK_HOME`、`DEVECO_JAVA_HOME` 和 `DEVECO_HVIGORW_JS` 仓库变量；HAP 作为工作流产物上传。
