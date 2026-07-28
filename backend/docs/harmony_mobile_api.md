# HarmonyOS 本地 API

## 启动

项目路径为 `D:\computer_vision_arkts`，鸿蒙工程路径为 `D:\arkts_project`。

PowerShell 执行策略阻止脚本时，可仅为本次命令使用 `-ExecutionPolicy Bypass`：

```powershell
cd D:\computer_vision_arkts
powershell -ExecutionPolicy Bypass -File .\start_harmony_api.ps1 `
  -PythonExe E:\Anaconda\envs\molecule-vision\python.exe `
  -ApiKey "请替换为你自己的本地密钥"
```

快速 demo：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_harmony_api.ps1 `
  -PythonExe E:\Anaconda\envs\molecule-vision\python.exe `
  -ApiKey "请替换为你自己的本地密钥" `
  -AppMode demo `
  -Backend demo
```

`-ApiKey` 必填，脚本将它设置为当前服务进程的 `HARMONY_API_KEY`。所有 `/api/v1/*` 请求必须使用请求头：

```text
X-API-Key: 与启动命令相同的密钥
```

注册和登录成功后，除健康检查和登录注册接口外，还需要携带会话令牌：

```text
Authorization: Bearer <登录返回的 token>
```

账号、密码哈希和会话保存在电脑端 SQLite。密码使用随机盐和 PBKDF2-HMAC-SHA256；原始密码不会写入数据库。会话有效期为 7 天，可通过退出登录立即撤销。

结构图和示例缩略图位于服务生成的签名 `/media/v1/*` 地址。这样 ArkUI `Image` 可以加载图片，同时 JSON 和所有数据修改接口仍受 API 密钥保护。

## 移动端接口

| 方法 | 地址 | 用途 |
|---|---|---|
| GET | `/api/v1/health` | 服务连接检测 |
| POST | `/api/v1/auth/register` | 注册本地组员账号并建立会话 |
| POST | `/api/v1/auth/login` | 登录并建立 7 天会话 |
| GET | `/api/v1/auth/me` | 当前账号资料 |
| PATCH/PUT | `/api/v1/auth/me` | 修改显示名称和组内角色 |
| POST | `/api/v1/auth/change-password` | 验证旧密码、撤销旧会话并签发新令牌 |
| GET | `/api/v1/auth/members` | 项目组成员列表 |
| POST | `/api/v1/auth/me/avatar` | 上传并裁剪当前账号头像 |
| POST | `/api/v1/auth/logout` | 撤销当前会话 |
| GET | `/api/v1/samples` | 内置示例列表 |
| POST | `/api/v1/jobs/images` | 上传图片并创建后台任务 |
| POST | `/api/v1/jobs/samples/{id}` | 创建内置示例任务 |
| GET | `/api/v1/jobs/{jobId}` | 查询任务状态和完成结果 |
| POST | `/api/v1/documents` | 上传 PDF、页面图片或 ZIP 文档 |
| POST | `/api/v1/documents/{id}/detect` | 创建区域检测后台任务 |
| GET | `/api/v1/documents/{id}` | 读取安全的页面预览与区域列表 |
| PATCH/PUT | `/api/v1/documents/{id}/regions/{regionId}` | 编辑 bbox、区域类型和确认状态 |
| POST | `/api/v1/documents/{id}/regions/{regionId}/recognize` | 人工确认 molecule 后创建单区域 OCSR 任务 |
| POST | `/api/v1/analyze-smiles` | 同步校验/分析 SMILES 并写入历史 |
| GET | `/api/v1/analyses?scope=all|mine&ownerUserId=...` | 共享历史、个人历史和创建者筛选 |
| GET | `/api/v1/analyses/{id}` | 读取结果详情、化学身份、图像质量、规则检查、模型轨迹与复核审计 |
| PATCH | `/api/v1/analyses/{id}/favorite` | 设置收藏 |
| PUT | `/api/v1/analyses/{id}/favorite` | HarmonyOS 网络组件兼容调用 |
| DELETE | `/api/v1/analyses/{id}` | 只删除 SQLite 索引 |
| POST | `/api/v1/analyses/{id}/review` | 确认、修正后确认、标记无法确认或撤销确认 |

结果详情在保留旧字段的基础上增加 `sourceImageUrl`、`identity`、`imageQuality`、
`lipinskiDetail`、`recognitionTrace` 和 `review`。`sourceImageUrl` 指向签名的
`/media/v1/analyses/{id}/input`，只允许读取该分析目录内的原始图片；手动 SMILES
结果或缺少原图的旧报告返回 `null`。响应不会包含电脑文件路径、运行目录、API 密钥、
登录令牌或完整内部报告。

复核请求示例：

```json
{
  "action": "unable_to_confirm",
  "reason": "image_unclear",
  "note": "原图局部模糊，需要重新上传。"
}
```

`action` 支持 `confirm`、`unable_to_confirm` 和 `revoke`。无法确认原因支持
`image_unclear`、`structure_incomplete`、`multiple_molecules`、
`model_result_unreliable` 和 `other`；使用 `other` 时必须填写不超过 300 字的备注。
旧客户端只提交 `{ "smiles": "...", "confirm": true }` 时仍按确认操作处理。

旧的 `/api/v1/analyze-image` 和 `/api/v1/analyze-sample/{id}` 同步接口保留，用于兼容旧前端。

文档检测复用 `src/documents` 的页面装载、候选检测、筛选和区域编辑审计。检测阶段不运行
OCSR；只有审核接口明确确认且区域类型为 `molecule` 时才创建识别任务。`reaction_like`
只返回反应流程分流提示，当前不会解析或进入单分子 OCSR。页面预览使用签名的
`/media/v1/documents/{id}/pages/{pageNumber}`，响应不暴露电脑本地路径。

## 任务与历史

- 任务 JSON 保存在 `data\api_jobs`，单工作线程串行复用已加载模型。
- 文档上传清单保存在 `data\mobile_documents`，页面、区域与审核审计继续使用现有文档结果 JSON。
- 移动端前台每 2 秒查询一次；切出识别 Tab 后停止查询，返回后继续。
- 活动任务 ID、服务地址和界面状态保存在 HarmonyOS Preferences。
- API 密钥和登录令牌保存在 HarmonyOS Asset Store；首次升级会迁移并清除 Preferences 中的旧敏感值。密码不保存在移动端。
- 每名组员使用独立账号、姓名、角色和头像；头像保存在 `data\avatars`。
- 新建图片任务和 SMILES 分析会记录创建者；旧记录继续保留并显示为“历史数据”。
- 历史为全组共享，支持“全组 / 我的 / 指定组员”筛选；收藏、复核和索引删除仍为团队协作操作。
- 服务重启后，已有成功报告的任务恢复为 `completed`；其余未完成任务标记为 `failed` 并提示重新提交。
- 图片和 SMILES 结果统一写入电脑端 SQLite 历史。
- 删除历史只移除 SQLite 索引，不删除 `data\runs` 中的报告、输入图片和结构图。
- 图片复核先校验 SMILES。非法输入返回 422 且不写报告；合法修正会重绘结构、重新计算性质并标记人工确认。
- 每次确认、撤销或无法确认都会在报告 JSON 中记录操作组员、角色、时间、SMILES、原因和备注，SQLite 无需新增审计表。
- ADMET 未启用或不可用时返回明确状态和说明，不生成预测值。

## 模拟器连接

鸿蒙端默认地址为 `http://192.168.5.13:8000`。电脑 IP 变化后，用 `ipconfig` 查看当前 IPv4 地址并在应用“设置”页修改。模拟器内的 `127.0.0.1` 不是电脑；服务必须监听 `0.0.0.0`，Windows 防火墙也必须允许 8000 端口通信。

模拟器图库为空属于正常情况，可在“识别”页使用内置示例完成完整流程。

## 验证

后端 API 测试：

```powershell
cd D:\computer_vision_arkts
E:\Anaconda\envs\molecule-vision\python.exe -m pip install -r requirements-api.txt
E:\Anaconda\envs\molecule-vision\python.exe -m pytest tests\test_harmony_api.py -q
```

ArkTS/HAP 构建：

```powershell
cd D:\arkts_project
$env:DEVECO_SDK_HOME='E:\DevEco Studio\sdk'
$env:JAVA_HOME='E:\DevEco Studio\jbr'
& 'E:\DevEco Studio\tools\node\node.exe' `
  'E:\DevEco Studio\tools\hvigor\bin\hvigorw.js' `
  assembleHap --mode module -p product=default -p module=entry@default -p buildMode=debug --no-daemon
```

ArkTS 本地单元测试：

```powershell
& 'E:\DevEco Studio\tools\node\node.exe' `
  'E:\DevEco Studio\tools\hvigor\bin\hvigorw.js' `
  test --mode module -p product=default -p module=entry@default -p buildMode=debug --no-daemon
```
