"""Files researchers upload: a project's file area (shared by its runs) and a run's own files.

Uploads are sent as the raw request body (one file per request, its path in the query), so
large files stream to disk as they arrive; folders are uploaded file by file with their
relative paths.  Archives are kept as they are unless ``extract`` is asked for.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import tarfile
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from aurora.api.routes import campaigns as c

router = APIRouter(prefix="/api/campaigns", tags=["files"])

_ARCHIVES = (".zip", ".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz")
_GB = 1024 ** 3


def project_files_dir(campaign_id: str) -> Path:
    return c._projects_root() / campaign_id / "files"


def clean_rel(path: str) -> str:
    """A safe relative path: no '..', no absolute paths, no control characters."""
    parts = [p for p in path.replace("\\", "/").strip("/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." or any(ord(ch) < 32 for ch in p) or len(p) > 255 for p in parts):
        raise HTTPException(400, f"Invalid path: {path!r}")
    return "/".join(parts)


def _inside(base: Path, rel: str) -> Path:
    target = (base / rel).resolve()
    if not target.is_relative_to(base.resolve()):
        raise HTTPException(400, "Invalid path")
    return target


def _limits() -> tuple[int, int]:
    from aurora.config import AuroraConfig
    up = AuroraConfig.model_validate({**c.base_config(), "objective": "uploads"}).uploads
    return int(up.max_file_gb * _GB), int(up.max_project_gb * _GB)


def _entry(path: Path, base: Path) -> dict[str, Any]:
    st = path.stat()
    return {"name": path.name, "path": str(path.relative_to(base)), "type": "dir" if path.is_dir() else "file",
            "size": st.st_size if path.is_file() else None, "modified": st.st_mtime}


async def _receive(request: Request, target: Path, *, max_file: int, room: int | None) -> int:
    """Stream the request body to *target* (through a temporary file); the bytes written."""
    declared = int(request.headers.get("content-length") or 0)
    if declared > max_file:
        raise HTTPException(413, f"The file is larger than the limit of {max_file / _GB:g} GB")
    if room is not None and declared > room:
        raise HTTPException(413, f"Not enough room: the project may take {room / _GB:.2f} GB more")
    existing = target.parent
    while not existing.exists() and existing != existing.parent:
        existing = existing.parent
    free = shutil.disk_usage(existing).free
    if declared and declared + _GB > free:
        raise HTTPException(507, f"Not enough disk space ({free / _GB:.1f} GB free)")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex[:8]}.part")
    size = 0
    try:
        with open(tmp, "wb") as fh:
            async for chunk in request.stream():
                size += len(chunk)
                if size > max_file or (room is not None and size > room):
                    raise HTTPException(413, "The upload went over the limit")
                fh.write(chunk)
        tmp.replace(target)
    finally:
        tmp.unlink(missing_ok=True)
    return size


def _extract(archive: Path) -> Path:
    """Unpack an archive next to it, into a folder of its name (safely), and remove the archive."""
    name = archive.name
    for ext in sorted(_ARCHIVES, key=len, reverse=True):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    dest = archive.with_name(name or "archive")
    if dest.exists():
        dest = archive.with_name(f"{name}-{uuid.uuid4().hex[:4]}")
    dest.mkdir(parents=True)
    root = dest.resolve()
    if archive.name.lower().endswith(".zip"):
        with zipfile.ZipFile(archive) as zf:
            for member in zf.infolist():
                out = (dest / member.filename).resolve()
                if not out.is_relative_to(root) or (member.external_attr >> 16) & 0o170000 == 0o120000:
                    continue        # outside the folder, or a symbolic link: skipped
                if member.is_dir():
                    out.mkdir(parents=True, exist_ok=True)
                else:
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(member) as src, open(out, "wb") as dst:
                        shutil.copyfileobj(src, dst)
    else:
        with tarfile.open(archive) as tf:
            tf.extractall(dest, filter="data")      # refuses paths outside dest, links out, devices
    archive.unlink()
    return dest


# ---------------------------------------------------------------------------
# A project's files
# ---------------------------------------------------------------------------

@router.get("/{campaign_id}/files")
async def list_project_files(campaign_id: str, path: str = "") -> dict[str, Any]:
    """A folder of the project's files, with the space they take and the limits."""
    c._ensure_campaign(campaign_id)
    base = project_files_dir(campaign_id)
    folder = _inside(base, clean_rel(path)) if path.strip("/") else base
    entries = []
    if folder.is_dir():
        entries = [_entry(p, base) for p in sorted(folder.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
                   if not p.name.startswith(".")]
    max_file, max_project = _limits()
    usage = await asyncio.to_thread(c._dir_size, base) if base.exists() else 0
    return {"path": str(folder.relative_to(base)) if folder != base else "", "entries": entries,
            "usage_bytes": usage, "max_file_bytes": max_file, "max_project_bytes": max_project}


@router.put("/{campaign_id}/files")
async def upload_project_file(campaign_id: str, path: str, request: Request, extract: bool = False) -> dict[str, Any]:
    """Upload one file (the request body) to the project's files at *path*; with *extract*, an
    archive is unpacked into a folder of its name."""
    campaign = c._ensure_campaign(campaign_id)
    base = project_files_dir(campaign_id)
    target = _inside(base, clean_rel(path))
    if target.is_dir():
        raise HTTPException(409, f"{path} is a folder")
    max_file, max_project = _limits()
    used = await asyncio.to_thread(c._dir_size, base) if base.exists() else 0
    old = target.stat().st_size if target.is_file() else 0
    size = await _receive(request, target, max_file=max_file, room=max_project - used + old)
    result = _entry(target, base)
    if extract and target.name.lower().endswith(_ARCHIVES):
        try:
            folder = await asyncio.to_thread(_extract, target)
        except (zipfile.BadZipFile, tarfile.TarError, OSError) as exc:
            raise HTTPException(400, f"Could not unpack {target.name}: {exc}") from exc
        result = {**_entry(folder, base), "extracted_from": target.name}
    c._touch(campaign)
    return {**result, "bytes": size}


@router.post("/{campaign_id}/files/folder")
async def make_project_folder(campaign_id: str, path: str) -> dict[str, Any]:
    c._ensure_campaign(campaign_id)
    base = project_files_dir(campaign_id)
    folder = _inside(base, clean_rel(path))
    folder.mkdir(parents=True, exist_ok=True)
    return _entry(folder, base)


@router.delete("/{campaign_id}/files")
async def delete_project_file(campaign_id: str, path: str) -> dict[str, Any]:
    c._ensure_campaign(campaign_id)
    base = project_files_dir(campaign_id)
    target = _inside(base, clean_rel(path))
    if not target.exists():
        raise HTTPException(404, f"No file {path}")
    if target.is_dir():
        await asyncio.to_thread(shutil.rmtree, target)
    else:
        target.unlink()
    return {"ok": True, "path": path}


@router.get("/{campaign_id}/files/raw")
async def raw_project_file(campaign_id: str, path: str) -> Any:
    """A project file as it is (to open a PDF, say)."""
    from fastapi.responses import FileResponse
    c._ensure_campaign(campaign_id)
    target = _inside(project_files_dir(campaign_id), clean_rel(path))
    if not target.is_file():
        raise HTTPException(404, "No such file")
    return FileResponse(target, filename=target.name, content_disposition_type="inline")


def read_preview(path: Path, limit: int = 20000) -> dict[str, Any]:
    """The start of a text file (for the front desk and the console), or a note for a binary one."""
    with open(path, "rb") as fh:
        head = fh.read(limit + 1)
    size = path.stat().st_size
    if b"\x00" in head[:4096]:
        return {"binary": True, "size": size}
    text = head[:limit].decode(errors="replace")
    return {"binary": False, "size": size, "content": text, "truncated": size > limit}


@router.get("/{campaign_id}/files/preview")
async def preview_project_file(campaign_id: str, path: str) -> dict[str, Any]:
    c._ensure_campaign(campaign_id)
    target = _inside(project_files_dir(campaign_id), clean_rel(path))
    if not target.is_file():
        raise HTTPException(404, f"No file {path}")
    return {"path": path, **read_preview(target)}


# ---------------------------------------------------------------------------
# Files of one run (uploaded into its directory; /app while it runs)
# ---------------------------------------------------------------------------

@router.put("/{campaign_id}/experiments/{exp_id}/workspace/upload")
async def upload_run_file(campaign_id: str, exp_id: str, path: str, request: Request) -> dict[str, Any]:
    """Upload one file into the run: *path* is relative to its directory (``workspace/`` is the
    container's /app), or ``@container/<path>`` for the running container's /app directly."""
    run = c._get_run(campaign_id, exp_id)
    max_file, _ = _limits()
    if path.startswith(c._CONTAINER_PREFIX):
        live = c._container(run)
        if not live:
            raise HTTPException(409, "The container is not running")
        rel = clean_rel(path[len(c._CONTAINER_PREFIX):])
        mounted = getattr(live[0], "mounted", False)
        if mounted:     # /app is the run's workspace directory: write it there
            target = _inside(run.campaign_dir / "workspace", rel)
            size = await _receive(request, target, max_file=max_file, room=None)
            return {"path": path, "bytes": size}
        staging = run.campaign_dir / ".uploads" / uuid.uuid4().hex[:8] / Path(rel).name
        size = await _receive(request, staging, max_file=max_file, room=None)
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "exec", live[1], "mkdir", "-p", str(Path("/app") / Path(rel).parent),
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
            await proc.wait()
            proc = await asyncio.create_subprocess_exec(
                "docker", "cp", str(staging), f"{live[1]}:/app/{rel}", stderr=asyncio.subprocess.PIPE)
            _, err = await proc.communicate()
            if proc.returncode != 0:
                raise HTTPException(500, err.decode().strip() or "docker cp failed")
        finally:
            shutil.rmtree(staging.parent, ignore_errors=True)
        return {"path": path, "bytes": size}
    target = _inside(run.campaign_dir, clean_rel(path))
    size = await _receive(request, target, max_file=max_file, room=None)
    return {"path": path, "bytes": size}


# ---------------------------------------------------------------------------
# Giving project files to a run
# ---------------------------------------------------------------------------

PROJECT_PREFIX = "project:"


def resolve_input(campaign_id: str, item: str) -> str:
    """An input of an experiment: ``project:<path>`` is a file or folder of the project's files."""
    if not item.startswith(PROJECT_PREFIX):
        return item
    target = _inside(project_files_dir(campaign_id), clean_rel(item[len(PROJECT_PREFIX):]))
    if not target.exists():
        raise HTTPException(400, f"No project file {item[len(PROJECT_PREFIX):]}")
    return str(target)


def project_files_summary(campaign_id: str, limit: int = 40) -> list[str]:
    """The project's files (path and size), for the front desk's status."""
    base = project_files_dir(campaign_id)
    if not base.is_dir():
        return []
    out = []
    for root, dirs, files in os.walk(base):
        dirs[:] = sorted(d for d in dirs if not d.startswith("."))
        for f in sorted(files):
            if f.startswith("."):
                continue
            p = Path(root) / f
            out.append(f"{p.relative_to(base)} ({_human(p.stat().st_size)})")
            if len(out) >= limit:
                return out + ["..."]
    return out


def _human(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def note_upload(campaign_id: str, names: list[str]) -> None:
    """Say in the project chat what was uploaded (the front desk sees the files in its status anyway)."""
    camp = c._ensure_campaign(campaign_id)
    shown = ", ".join(names[:8]) + (f" and {len(names) - 8} more" if len(names) > 8 else "")
    camp.note(f"Uploaded to the project's files: {shown}")
    c._touch(camp)


@router.post("/{campaign_id}/files/uploaded")
async def files_uploaded(campaign_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """The console reports a finished upload of several files (one note in the project chat)."""
    names = [str(n) for n in (body.get("names") or [])][:200]
    if names:
        note_upload(campaign_id, names)
    return {"ok": True, "at": time.time()}
