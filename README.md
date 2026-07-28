# Molecule Vision：Python 后端与 HarmonyOS 前端

这是一个面向分子结构图像识别与性质分析的端到端项目。电脑端 Python 服务负责 OCSR 推理、RDKit 校验、结果持久化、团队账号和人工复核；HarmonyOS ArkTS 应用负责图片/内置示例识别、SMILES 分析、共享历史、结果详情和团队设置。

## 仓库结构

- `backend/`：Python/FastAPI 服务、识别与分析管线、SQLite 存储和测试。
- `harmony_app/`：HarmonyOS ArkTS 手机端工程，可直接用 DevEco Studio 打开。

## 快速启动

先准备 Python 3.10 环境并安装后端依赖：

```powershell
cd backend
python -m pip install -r requirements.txt -r requirements-api.txt
```

演示模式不加载大型识别模型，适合先联调鸿蒙模拟器：

```powershell
powershell -ExecutionPolicy Bypass -File .\start_harmony_api.ps1 `
  -PythonExe "<你的 Python 解释器路径>" `
  -ApiKey "<自行设置的本地密钥>" `
  -AppMode demo `
  -Backend demo
```

`ApiKey` 不是平台分配的账号密钥，而是你自己为本机服务设置的一段字符串。应用设置页必须填写同一内容。不要把真实密钥提交到仓库。

然后用 DevEco Studio 打开 `harmony_app/`，启动手机模拟器并运行 `entry`。在登录页或设置页填写：

- 服务地址：`http://<电脑局域网 IPv4>:8000`
- API 密钥：与启动命令中的 `-ApiKey` 完全相同

模拟器的 `127.0.0.1` 指向模拟器自身，不能用来访问电脑。电脑防火墙需要允许 Python 访问 TCP 8000 端口。

## 验证

```powershell
cd backend
python -m pytest tests\test_harmony_api.py -q
```

鸿蒙端可在 DevEco Studio 中运行测试和 `assembleHap`。更完整的接口与模拟器说明见 [`backend/docs/harmony_mobile_api.md`](backend/docs/harmony_mobile_api.md) 和 [`harmony_app/README.md`](harmony_app/README.md)。

## 安全与数据

仓库不包含 API 密钥、登录令牌、模型权重、本地数据库、推理报告、上传图片、IDE 配置或 HAP 构建产物。真实运行产生的数据默认保留在电脑端，提交代码前请继续检查敏感信息。
