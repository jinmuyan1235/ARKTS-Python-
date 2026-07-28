"""Chinese project explanation page."""

from __future__ import annotations

import streamlit as st

from src.ui.styles import page_intro


def render_about_page() -> None:
    page_intro("项目说明", "本系统用于分子结构图片识别、SMILES 校验、性质计算、文档区域检测和结果导出。")
    st.markdown(
        """
### 后端说明

- **演示模式**：只按内置样例文件名返回固定 SMILES，不是真实 AI 图像识别。
- **MolScribe**：真实 OCSR 后端，需要安装包并配置模型权重。
- **DECIMER**：真实 OCSR 后端，需要 DECIMER/TensorFlow 环境；CPU 批量处理较慢。
- **多模型联合识别**：同时比较多个真实后端的候选结果，不直接比较未校准置信度。

### 核心组件

- **Hybrid 文档检测器**：优先接入可训练版面检测器，未配置时使用 OpenCV 启发式 fallback。
- **RDKit**：用于 SMILES 解析、标准化、结构重绘、性质计算和规则判断。
- **PDF 文档区域检测**：将 PDF 或页面图渲染为图片，检测分子结构候选区域，再按区域执行 OCSR。

### 当前限制

- 启发式区域检测不是训练模型，复杂论文页面需要人工确认和编辑 bbox。
- `reaction_arrow`、`reaction_condition` 和 `reaction_like` 区域只会分流到人工确认，本系统暂不解析化学反应。
- demo 结果不能代表真实识别能力。
- RDKit 与规则判断只用于数据整理和教学演示，不能替代实验或专业结论。
"""
    )
