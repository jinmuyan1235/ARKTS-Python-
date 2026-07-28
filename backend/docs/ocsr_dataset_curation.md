# OCSR 真实测试集收集与标注流程

目标不是堆图片数量，而是得到一个能揭示真实失败模式、可复现、可审计的验收集。

## 1. 数据集分层

建议至少维护三类数据：

- `generated_acceptance`：本仓库脚本用 RDKit 生成并加扰动的验收集，用于快速回归和覆盖边界场景。
- `starter_real_acceptance`：`data/ocsr_real_acceptance/` 中的可复现 smoke benchmark，用固定上游 URL 和 SHA-256 重建图片，只用于验证真实评测框架。
- `manual_labeled_real`：你从允许使用的论文、专利、网页截图或实验材料中截取并人工标注的真实图片。
- `negative_controls`：文字、表格、反应式、空白区域、图注等应拒识的非分子区域。

`generated_acceptance` 和 `starter_real_acceptance` 都不能当成真实 OCSR 准确率，只用于发现明显工程回归和验证评测框架。最终汇报准确率时应优先引用规模足够、独立来源足够、许可清晰且人工审核完成的 `manual_labeled_real`。

## 2. 生成本地验收包

```bash
python scripts/build_ocsr_acceptance_set.py
```

默认输出：

```text
data/ocsr_acceptance/
├── images/
└── manifest.csv
```

然后运行：

```bash
python scripts/evaluate_ocsr.py \
  --manifest data/ocsr_acceptance/manifest.csv \
  --dataset-root data/ocsr_acceptance \
  --backend molscribe \
  --output data/outputs/benchmark
```

生成的 manifest 覆盖干净结构图、低分辨率、旋转、模糊噪声、JPEG 压缩、非白背景和拒识负样本。

## 3. 重建 starter 真实验收集

`data/ocsr_real_acceptance/manifest.csv` 引用的图片不提交到 Git；它们由确定性下载器从固定上游版本重建：

```bash
python scripts/download_real_acceptance_set.py
python scripts/validate_real_acceptance_set.py
python scripts/run_release_acceptance.py \
  --release-version starter-v0.1 \
  --manifest data/ocsr_real_acceptance/manifest.csv \
  --dataset-root data/ocsr_real_acceptance \
  --backends molscribe,ensemble
```

`source_manifest.csv` 必须记录固定 URL、上游版本、来源 SHA-256、最终图片 SHA-256、许可说明和派生操作。验证脚本必须在缺图片、路径逃逸、SHA 不符、InChIKey 不一致、verified 行缺 annotator/reviewer、或同一 `source_document` 跨 split 时失败。

该 starter 只有少量独立来源；扰动图不能作为独立样本统计。它不是 release-qualified 数据集，不能用于宣称真实世界准确率，也不能用于调阈值后再当独立测试集。

## 4. 人工收集真实图片

只收集你有权使用的来源。每张图建议保留：

- 原始来源 URL、DOI、专利号或内部文档 ID；
- 截图日期；
- 原始页面或图片文件；
- 裁剪后的分子区域；
- 由人工核对的 ground-truth SMILES；
- 是否应该识别：`expected_action=recognize` 或 `expected_action=reject`。

不要把无授权的论文截图或专有数据提交到公开仓库。可以只提交 manifest 模板、来源说明和不可公开数据的路径占位。

## 5. 标注字段

使用 `benchmark/manual_labels_template.csv` 作为起点。关键字段：

- `ground_truth_smiles`：识别目标的真值；拒识样本可为空。
- `expected_action`：`recognize` 或 `reject`。
- `category`：如 `literature_scan`、`patent_figure`、`hand_drawn`、`reaction_distractor`。
- `image_quality`：如 `clean`、`scanned`、`low_resolution`、`noisy_blurry`、`compressed`。
- `complexity`：`low`、`medium`、`high`、`none`。
- `perturbation`：如 `rotation`、`jpeg_compression`、`background_tint`、`none`。
- `structure_features`：分号分隔，如 `stereocenter;formal_charge;salt;macrocycle`。
- `scaffold_key` 和 `source_document`：用于防止训练集/测试集泄漏。

## 6. 导入人工标注图片

假设你把截图放在 `data/raw_ocsr_real/raw/`，并填好 `data/raw_ocsr_real/labels.csv`：

```bash
python scripts/ingest_ocsr_labeled_images.py \
  --labels data/raw_ocsr_real/labels.csv \
  --image-root data/raw_ocsr_real \
  --output-root data/ocsr_manual_labeled
```

脚本会复制图片、生成 `data/ocsr_manual_labeled/manifest.csv`，并调用 manifest 校验。校验失败时会报告具体行号和字段。

## 7. 拆分原则

不要随机按图片行拆分。优先按这些维度隔离：

- 分子身份；
- scaffold；
- 来源文档或专利族；
- 生成工具或截图批次；
- 高相似结构族。

同一个分子或高度相似的衍生图不要同时出现在训练集和测试集。真实验收集建议固定 `split=test`，不要在调参时反复查看后手动优化。

## 8. 验收报告怎么看

`evaluate_ocsr.py` 会输出：

- valid SMILES rate（正样本 RDKit 有效预测数 / 正样本数）；
- canonical exact match rate（正样本 canonical exact 数 / 正样本数）；
- stereochemistry exact rate；
- atom count / formal charge / bond type profile error rate；
- confidence calibration error；
- rejection coverage（正确拒绝的负样本数 / 负样本数）；
- negative hallucination rate（负样本产生 RDKit 有效结构数 / 负样本数）；
- false accept rate（负样本被自动 accepted 且无需人工审核数 / 负样本数）；
- 数据集充分性统计，包括正/负样本数、独立来源文档数、唯一分子/scaffold 数、已验证比例、缺图和 checksum 错误。
- 按 source、image_quality、complexity、perturbation、structure_features 分层的指标。

如果总体准确率看起来不错，但 `scanned`、`compressed`、`stereocenter` 或 `reject` 分层很差，仍不能声明系统已经达到真实可用。

## 9. 从人工纠错回流数据

单图识别页支持两种数据回流动作：

- `仅保存纠错`：把原图、预测、修正 SMILES 和元数据保存为待审核反馈，不进入训练/评估 manifest。
- `确认进入训练集`：保存反馈并标记为 `review_status=verified`、`include_in_training=true`，后续可导出为训练或评估 manifest。

反馈目录结构：

```text
data/feedback/
├── images/
│   └── <sha256>.png
├── annotations/
│   └── <analysis_id>.json
└── manifest.csv
```

每条 annotation 至少包含：

- `image_sha256` 和归档后的 `image_path`；
- `predicted_smiles`、`corrected_smiles`；
- `backend`、`model_name`、`model_version`、`model_sha256`、`device`；
- `correction_type`：`atom`、`bond`、`charge`、`stereo`、`missing_fragment` 或 `other`；
- `review_status`：`pending`、`verified` 或 `rejected`；
- `source_reference`、`source_license`、`privacy_notes` 和备注。

同一张图片会按 SHA-256 去重；重复图片仍会保存 annotation，但 `manifest.csv` 会标记 `duplicate_image=true` 和 `duplicate_of`。

导出已审核反馈：

```bash
python scripts/export_feedback_manifest.py \
  --feedback-root data/feedback \
  --output data/feedback/verified_manifest.csv \
  --split train
```

默认只导出 `review_status=verified` 且 `include_in_training=true` 的记录，并跳过重复图片。需要保留重复样本时加 `--keep-duplicates`。
