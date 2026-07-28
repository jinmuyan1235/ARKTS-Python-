# API 设计

## `ImagePreprocessor`

- `load_image(source) -> np.ndarray`
- `to_grayscale(image) -> np.ndarray`
- `denoise(image) -> np.ndarray`
- `binarize(image) -> np.ndarray`
- `crop_whitespace(image) -> np.ndarray`
- `deskew(image) -> np.ndarray`
- `resize_normalize(image, size) -> np.ndarray`
- `preprocess_pipeline(source) -> dict[str, np.ndarray]`

## OCSR

`BaseOCSRAdapter.recognize(image_path_or_array) -> OCSRResult`

`OCSRResult` 包含 `smiles`、`confidence`、`backend`、`status` 和 `message`。可用后端为 `demo`、`molscribe`、`decimer`。

- `BaseOCSRAdapter.status() -> {backend, available, message}`
- `MoleculeRecognizer.status() -> {backend, available, message}`

## 化学模块

- `validate_smiles(smiles) -> dict`
- `canonicalize_smiles(smiles) -> str | None`
- `calculate_descriptors(smiles) -> dict`
- `evaluate_lipinski(descriptors) -> dict`
- `draw_molecule(smiles, output_path) -> str`

## 分析服务

### `MoleculeReportGenerator.generate`

只接受 `image_path` 或 `smiles` 之一，返回统一报告字典。每份报告包含唯一 `analysis_id`，避免重复文件名覆盖输出。`status=success` 表示校验、描述符和结构重绘全部成功；可选 ADMET 的禁用或不可用不会改变主流程状态。

### `BatchAnalyzer.analyze_folder`

输入文件夹，返回 `summary`、`rows`、`dataframe`、`reports` 和 `exports`。输出目录包含 CSV、JSON、统计图、预处理图和结构重绘图。

## 导出

- `save_json(data, output_path) -> str`
- `save_csv(rows, output_path) -> str`
- `save_pdf(report, output_path) -> {success, path, message}`

## 可选 ADMET baseline

- `smiles_to_fingerprint(smiles, radius=2, n_bits=2048) -> np.ndarray`
- `ADMETBaseline.train(smiles_values, labels, target_name, task_type) -> ADMETBaseline`
- `ADMETBaseline.predict(smiles) -> dict`
- `ADMETBaseline.save(output_path) -> str`
- `ADMETBaseline.load(model_path) -> ADMETBaseline`
- `ConfiguredADMETPredictor.predict(smiles) -> dict`

`ConfiguredADMETPredictor` 返回 `disabled`、`unavailable`、`failed` 或 `success`，并与 RDKit 主流程隔离。
