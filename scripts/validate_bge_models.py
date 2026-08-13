"""离线验证 P0-08 固定 BGE-M3 与 Reranker 模型快照。"""

from __future__ import annotations

import argparse
import importlib
import json
import platform
import time
from pathlib import Path
from typing import Any

EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
EMBEDDING_LICENSE = "MIT"
EMBEDDING_DIMENSION = 1024
RERANKER_MODEL = "BAAI/bge-reranker-v2-m3"
RERANKER_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
RERANKER_LICENSE = "Apache-2.0"

QUERY = "差旅报销多少天内提交"
PASSAGES = (
    "实验室访客进入办公区前必须登记合成访客编号。",
    "差旅报销必须在三十天内提交, 并附合成票据。",
    "合成设备采购申请需要由部门负责人审批。",
)
EXPECTED_PASSAGE_INDEX = 1


def snapshot_path(cache_dir: Path, model_id: str, revision: str) -> Path:
    model_directory = f"models--{model_id.replace('/', '--')}"
    return cache_dir / "hub" / model_directory / "snapshots" / revision


def elapsed_seconds(started_at: float) -> float:
    return round(time.perf_counter() - started_at, 3)


def dense_embeddings(
    torch_module: Any,
    model: Any,
    tokenizer: Any,
    texts: tuple[str, ...],
) -> Any:
    encoded = tokenizer(
        list(texts),
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="pt",
    )
    with torch_module.inference_mode():
        output = model(**encoded)
    # BGE-M3 的 dense 表示取首个 Token，并归一化后用于 cosine 检索。
    return torch_module.nn.functional.normalize(output.last_hidden_state[:, 0], p=2, dim=1)


def reranker_scores(
    torch_module: Any,
    model: Any,
    tokenizer: Any,
    query: str,
    passages: tuple[str, ...],
) -> Any:
    encoded = tokenizer(
        [(query, passage) for passage in passages],
        padding=True,
        truncation=True,
        max_length=512,
        return_tensors="pt",
    )
    with torch_module.inference_mode():
        logits = model(**encoded).logits.reshape(-1)
    return torch_module.sigmoid(logits)


def validate(cache_dir: Path) -> dict[str, object]:
    # 模型依赖只在显式验收时加载，默认开发门禁和生产镜像无需安装 4 GB 模型栈。
    torch_module = importlib.import_module("torch")
    transformers = importlib.import_module("transformers")
    embedding_path = snapshot_path(cache_dir, EMBEDDING_MODEL, EMBEDDING_REVISION)
    reranker_path = snapshot_path(cache_dir, RERANKER_MODEL, RERANKER_REVISION)
    for path in (embedding_path, reranker_path):
        if not (path / "config.json").is_file():
            raise FileNotFoundError(f"固定模型快照不存在或不完整: {path}")

    started_at = time.perf_counter()
    embedding_tokenizer = transformers.AutoTokenizer.from_pretrained(
        embedding_path,
        local_files_only=True,
    )
    embedding_model = transformers.AutoModel.from_pretrained(
        embedding_path,
        local_files_only=True,
    ).eval()
    embedding_load_seconds = elapsed_seconds(started_at)

    started_at = time.perf_counter()
    embeddings = dense_embeddings(
        torch_module,
        embedding_model,
        embedding_tokenizer,
        (QUERY, *PASSAGES),
    )
    embedding_inference_seconds = elapsed_seconds(started_at)
    if embeddings.shape != (len(PASSAGES) + 1, EMBEDDING_DIMENSION):
        raise RuntimeError(f"BGE-M3 输出维度不符合索引契约: {tuple(embeddings.shape)}")
    norms = torch_module.linalg.vector_norm(embeddings, dim=1)
    if not torch_module.allclose(norms, torch_module.ones_like(norms), atol=1e-5):
        raise RuntimeError("BGE-M3 输出未按 cosine 检索要求归一化")
    embedding_scores = torch_module.matmul(embeddings[1:], embeddings[0])
    embedding_top_index = int(torch_module.argmax(embedding_scores).item())
    if embedding_top_index != EXPECTED_PASSAGE_INDEX:
        raise RuntimeError("BGE-M3 未将正确合成证据排在第一位")

    # Embedding 模型已完成验收，及时释放内存再加载 Reranker，适配普通开发机。
    del embedding_model, embedding_tokenizer, embeddings

    started_at = time.perf_counter()
    reranker_tokenizer = transformers.AutoTokenizer.from_pretrained(
        reranker_path,
        local_files_only=True,
    )
    reranker_model = transformers.AutoModelForSequenceClassification.from_pretrained(
        reranker_path,
        local_files_only=True,
    ).eval()
    reranker_load_seconds = elapsed_seconds(started_at)

    started_at = time.perf_counter()
    ranked_scores = reranker_scores(
        torch_module,
        reranker_model,
        reranker_tokenizer,
        QUERY,
        PASSAGES,
    )
    reranker_inference_seconds = elapsed_seconds(started_at)
    reranker_top_index = int(torch_module.argmax(ranked_scores).item())
    if reranker_top_index != EXPECTED_PASSAGE_INDEX:
        raise RuntimeError("BGE Reranker 未将正确合成证据排在第一位")

    return {
        "状态": "通过",
        "运行环境": {
            "系统": platform.platform(),
            "处理器": platform.machine(),
            "Torch": torch_module.__version__,
            "MPS 可用": torch_module.backends.mps.is_available(),
        },
        "Embedding": {
            "模型": EMBEDDING_MODEL,
            "Revision": EMBEDDING_REVISION,
            "许可证": EMBEDDING_LICENSE,
            "维度": EMBEDDING_DIMENSION,
            "加载秒数": embedding_load_seconds,
            "推理秒数": embedding_inference_seconds,
            "相似度": [round(float(value), 6) for value in embedding_scores],
            "正确证据 Top 1": embedding_top_index == EXPECTED_PASSAGE_INDEX,
        },
        "Reranker": {
            "模型": RERANKER_MODEL,
            "Revision": RERANKER_REVISION,
            "许可证": RERANKER_LICENSE,
            "加载秒数": reranker_load_seconds,
            "推理秒数": reranker_inference_seconds,
            "相关概率": [round(float(value), 6) for value in ranked_scores],
            "正确证据 Top 1": reranker_top_index == EXPECTED_PASSAGE_INDEX,
        },
        "数据声明": "查询与候选证据均为固定合成文本, 未发送到外部服务。",
        "结果边界": "本结果只证明小规模 CPU 功能链路, 不构成容量或并发认证。",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="离线验证固定 BGE 模型快照")
    parser.add_argument(
        "--cache-dir",
        type=Path,
        required=True,
        help="Hugging Face 本地缓存根目录, 例如 .ai-platform/models/huggingface",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    report = validate(args.cache_dir.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
