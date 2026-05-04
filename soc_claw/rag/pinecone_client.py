import logging
import os
import time

from pinecone import Pinecone, ServerlessSpec

_logger = logging.getLogger("soc-claw.rag.pinecone")

DEFAULT_INDEX_NAME = "soc-claw-playbooks"
_DEFAULT_CLOUD = "aws"
_DEFAULT_REGION = "us-east-1"


def _controller_client() -> Pinecone:
    api_key = os.getenv("PINECONE_API_KEY", "local")
    host = os.getenv("PINECONE_HOST")
    if not host:
        raise RuntimeError("PINECONE_HOST is not set")
    return Pinecone(api_key=api_key, host=host)


def _wait_for_controller(pc: Pinecone, timeout_s: int = 60) -> None:
    deadline = time.time() + timeout_s
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            pc.list_indexes()
            return
        except Exception as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"Pinecone controller not ready: {last_error}")


def ensure_index(
    index_name: str,
    dimension: int,
    metric: str = "cosine",
    *,
    wait: bool = True,
    timeout_s: int = 60,
) -> Pinecone:
    pc = _controller_client()
    if wait:
        _wait_for_controller(pc, timeout_s=timeout_s)
    if pc.has_index(index_name):
        return pc
    try:
        pc.create_index(
            name=index_name,
            dimension=dimension,
            metric=metric,
            spec=ServerlessSpec(
                cloud=os.getenv("PINECONE_CLOUD", _DEFAULT_CLOUD),
                region=os.getenv("PINECONE_REGION", _DEFAULT_REGION),
            ),
            deletion_protection="disabled",
        )
    except Exception as exc:
        if "already exists" in str(exc).lower():
            return pc
        raise
    return pc


def get_pinecone_index(index_name: str | None = None):
    index_name = index_name or os.getenv("PINECONE_INDEX_NAME", DEFAULT_INDEX_NAME)
    pc = _controller_client()
    index_host = pc.describe_index(name=index_name).host
    if not index_host.startswith(("http://", "https://")):
        index_host = f"http://{index_host}"
    return pc.Index(host=index_host)
