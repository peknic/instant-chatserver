import os
import re
from pathlib import Path

from .models import Chat

_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024


def file_root() -> Path:
    return Path(os.getenv("CHAT_FILE_DIR", "chatfiles")).resolve()


def max_file_size() -> int:
    return int(os.getenv("CHAT_MAX_FILE_SIZE", str(DEFAULT_MAX_FILE_SIZE)))


def _safe_segment(value: str, fallback: str) -> str:
    cleaned = _UNSAFE.sub("_", value or "").strip().strip(".")
    return cleaned or fallback


def chat_dirname(chat: Chat) -> str:
    return _safe_segment(chat.name or "", f"chat_{chat.id}")


def _chat_dir(chat: Chat) -> Path:
    directory = file_root() / chat_dirname(chat)
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _unique_target(directory: Path, filename: str) -> Path:
    stem = Path(filename).stem or "file"
    suffix = Path(filename).suffix
    candidate = directory / f"{stem}{suffix}"
    counter = 1
    while candidate.exists():
        candidate = directory / f"{stem}-{counter}{suffix}"
        counter += 1
    return candidate


def save_upload(chat: Chat, filename: str, data: bytes) -> str:
    safe_name = Path(filename or "file").name.replace("\\", "_") or "file"
    target = _unique_target(_chat_dir(chat), safe_name)
    target.write_bytes(data)
    return target.relative_to(file_root().parent).as_posix()


def resolve_file(chat: Chat, filename: str) -> Path | None:
    directory = (file_root() / chat_dirname(chat)).resolve()
    target = (directory / Path(filename or "").name).resolve()
    if target.parent != directory or not target.is_file():
        return None
    return target
