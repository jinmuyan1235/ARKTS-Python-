# Molecule Vision 鸿蒙移动端

该工程通过局域网 HTTP 调用 `D:\computer_vision_arkts` 中的 Python 视觉服务。鸿蒙端提供“识别、SMILES、历史、设置”四栏界面；DECIMER/MolScribe 推理、SQLite 历史和报告文件都保留在电脑端。

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
3. 首次打开会显示化学主题登录页。在登录卡片下方填写电脑服务地址 `http://192.168.5.13:8000` 和启动命令中的 API 密钥；电脑 IP 变化时用 `ipconfig` 查看并替换。
4. 点击“注册组员”，填写显示名称、组内角色、用户名和至少 8 位密码。
5. 注册成功后自动进入工作台，以后可以直接登录。
6. 每名组员注册自己的账号，并在“设置”页点击“更换头像”；头像和角色会显示在项目组成员区域。
7. 模拟器图库为空时，在“识别”栏直接点击“内置示例”即可完成识别。

密码不会保存在鸿蒙端，电脑端只保存 PBKDF2 加盐哈希。API 密钥和登录令牌保存在 HarmonyOS Asset Store；Preferences 只保存服务地址、活动任务 ID 和界面状态。

模拟器中的 `127.0.0.1` 指向模拟器自身，不能用来连接电脑。电脑和模拟器网络必须互通；Windows 防火墙提示出现时，应允许 Python 在专用网络通信。

## 3. 移动端功能

- 识别：内置示例、图库选择、后台排队/推理状态和失败提示。
- 后台恢复：活动任务 ID 保存在 HarmonyOS Preferences；切换 Tab 时停止轮询，回到识别页或重启应用后继续查询。
- SMILES：输入单条 SMILES，电脑端校验、计算性质并写入历史。
- 历史：共享创建者标注、“全组 / 我的 / 组员”筛选、搜索、状态筛选、仅收藏、分页、详情、收藏和仅删除 SQLite 索引。
- 结果详情：上传原图与重绘结构对照、完整化学身份、图像质量、描述符、逐项 Lipinski、模型轨迹、告警和 ADMET 状态；专业信息按模块展开。
- 人工复核：支持直接确认、修正 SMILES 后确认、标记无法确认和撤销确认，并记录操作组员、时间、原因与备注；非法输入不会覆盖原报告。
- 结果分享：可复制 SMILES、生成不包含上传原图和敏感凭据的 PNG 品牌卡片，并通过系统分享面板发送。
- 设置：编辑显示名称和角色、更换头像、修改密码、查看组员、安全存储状态、服务连接检测和退出登录。
- 登录注册：本地账户、7 天登录会话、退出登录和启动时自动恢复。
- 组员资料：每个账号拥有独立姓名、角色和头像，设置页展示项目组成员。
- 现代实验室主题：浅色/深色自动跟随系统，使用分子网络背景、语义化状态色、统一实验室卡片和安全的结构图白色基底。
- 响应式界面：适配 320–480vp 手机宽度和系统字体放大；小屏结果性质卡片自动切换为单列。
- 统一状态体验：样例、历史、成员和结果页分别展示加载骨架、空状态、可读错误与重试入口。

历史和报告不在手机端重复存储。删除历史索引后，电脑端 `data\runs\<analysisId>` 中的报告和图片仍然保留。

## 4. 命令行构建

登录后可从“识别”页的“化学文档识别”卡片进入独立工作台。上传 PDF、PNG/JPG 或 ZIP
后先执行区域检测，再在区域审核页调整 bbox 和类型。只有手动开启确认的 `molecule`
区域可以提交 OCSR；`reaction_like` 仅分流并显示说明，不会解析。

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
