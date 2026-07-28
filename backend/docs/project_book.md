# 项目说明书

## 1. 项目概述

本项目面向医药研发、化学文献数字化、专利分析与实验室数据归档场景，将二维分子结构图片转换为可检索、可计算的 SMILES，并完成结构校验、重绘、基础性质计算与报告导出。

## 2. 需求与角色

- 研发人员：减少从文献图片手工录入结构的时间与错误。
- 数据管理员：获得统一的 canonical SMILES 与批量 CSV/JSON。
- 实训答辩人员：稳定展示计算机视觉预处理和完整工程闭环。

## 3. 系统流程

1. 校验并读取 PNG/JPG/JPEG；
2. 灰度化、去噪、Otsu 二值化、白边裁剪、旋转校正与尺寸归一化；
3. 通过统一适配器调用 demo、MolScribe 或 DECIMER；
4. RDKit 解析和 canonical SMILES 标准化；
5. 结构重绘并计算基础描述符；
6. 执行 Lipinski 与扩展可旋转键规则；
7. 在 Streamlit 展示并导出 JSON、CSV、可选 PDF。
8. 若用户提供可信的带标签数据和本地模型，可选执行 Morgan 指纹 + Random Forest ADMET baseline。

## 4. 关键设计

### OCSR 适配器

所有后端都实现 `BaseOCSRAdapter.recognize`，并统一返回 `OCSRResult`。真实模型依赖在类初始化阶段安全导入，缺失时返回可读失败信息，不导致 Web 应用崩溃。

### 错误边界

图像读取、预处理、模型识别、SMILES 校验、性质计算和导出分别处理异常。批处理会记录单图错误并继续处理其余文件。

### CPU 与 GPU

demo 和 RDKit 流程完全支持 CPU。MolScribe 可通过 `OCSR_DEVICE` 与 `MOLSCRIBE_MODEL_PATH` 配置，GPU 不是必需条件。

### 可选 ADMET 与输出隔离

ADMET baseline 默认关闭，没有模型时只保留 RDKit 规则分析。每次单分子分析生成唯一 `analysis_id`，预处理图、结构重绘和报告文件不会因同名输入互相覆盖。

## 5. 验收演示

1. 执行 `python scripts/make_demo_samples.py` 生成样例；
2. 运行 `python -m streamlit run app.py`，确保使用当前 Python 解释器中的 Streamlit；
3. 上传 `data/samples/aspirin.png`；
4. 展示预处理阶段、识别结果、canonical SMILES、结构重绘和性质；
5. 在 SMILES 页输入 `CCO`；
6. 在批量页处理 `data/samples` 并下载 CSV。
7. 执行 `pytest -q` 验证核心、适配器、导出和端到端流程。

## 6. 风险与局限

demo 只用于工程演示。真实 OCSR 的准确率受图像清晰度、分辨率、结构复杂度和模型版本影响。规则分析不构成药物活性、毒性或临床安全结论。
