"""File operation tools for agentic loops.

Provides read, write, edit, list, and search operations so agents
can inspect and modify code/data files in the task environment.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from aurora.llm.tool_runner import ToolSchema

logger = logging.getLogger(__name__)

_MAX_READ = 10000


def build_file_read_tool(working_dir: str) -> ToolSchema:
    """Tool to read file contents."""

    async def handler(params: dict[str, Any]) -> str:
        file_path = params.get("path", "")
        if not file_path:
            return json.dumps({"error": "No path specified"})

        full_path = Path(working_dir) / file_path if not os.path.isabs(file_path) else Path(file_path)

        if not full_path.exists():
            return json.dumps({"error": f"File not found: {file_path}"})
        if full_path.is_dir():
            return json.dumps({"error": f"Path is a directory: {file_path}. Use list_files instead."})

        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            return json.dumps({"error": f"Cannot read file: {exc}"})

        offset = params.get("offset", 0)
        limit = params.get("limit", None)

        lines = content.splitlines(keepends=True)
        total_lines = len(lines)

        if offset > 0 or limit is not None:
            end = offset + limit if limit else len(lines)
            lines = lines[offset:end]

        text = "".join(lines)
        if len(text) > _MAX_READ:
            text = text[:_MAX_READ] + f"\n... (truncated, {total_lines} total lines)"

        return json.dumps({
            "content": text,
            "total_lines": total_lines,
            "path": str(full_path),
        })

    return ToolSchema(
        name="read_file",
        description=(
            "Read the contents of a file. Supports line offset/limit for large files. "
            "Use this to inspect source code, configuration files, data files, etc."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (relative to task dir or absolute)"},
                "offset": {"type": "integer", "description": "Start reading from this line number (0-based)"},
                "limit": {"type": "integer", "description": "Maximum number of lines to read"},
            },
            "required": ["path"],
        },
        handler=handler,
    )


def build_file_write_tool(working_dir: str) -> ToolSchema:
    """Tool to write or overwrite file contents."""

    async def handler(params: dict[str, Any]) -> str:
        file_path = params.get("path", "")
        content = params.get("content", "")
        if not file_path:
            return json.dumps({"error": "No path specified"})

        full_path = Path(working_dir) / file_path if not os.path.isabs(file_path) else Path(file_path)

        try:
            full_path.parent.mkdir(parents=True, exist_ok=True)
            full_path.write_text(content, encoding="utf-8")
            return json.dumps({
                "success": True,
                "path": str(full_path),
                "bytes_written": len(content.encode("utf-8")),
            })
        except Exception as exc:
            return json.dumps({"error": f"Write failed: {exc}"})

    return ToolSchema(
        name="write_file",
        description=(
            "Write content to a file, creating it if it doesn't exist or overwriting if it does. "
            "Parent directories are created automatically."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path (relative to task dir or absolute)"},
                "content": {"type": "string", "description": "Content to write"},
            },
            "required": ["path", "content"],
        },
        handler=handler,
    )


def build_file_edit_tool(working_dir: str) -> ToolSchema:
    """Tool to perform search-and-replace edits on a file."""

    async def handler(params: dict[str, Any]) -> str:
        file_path = params.get("path", "")
        old_string = params.get("old_string", "")
        new_string = params.get("new_string", "")

        if not file_path:
            return json.dumps({"error": "No path specified"})
        if not old_string:
            return json.dumps({"error": "old_string is required"})

        full_path = Path(working_dir) / file_path if not os.path.isabs(file_path) else Path(file_path)

        if not full_path.exists():
            return json.dumps({"error": f"File not found: {file_path}"})

        try:
            content = full_path.read_text(encoding="utf-8")
        except Exception as exc:
            return json.dumps({"error": f"Cannot read file: {exc}"})

        count = content.count(old_string)
        if count == 0:
            return json.dumps({"error": "old_string not found in file", "path": str(full_path)})

        replace_all = params.get("replace_all", False)
        if not replace_all and count > 1:
            return json.dumps({
                "error": f"old_string found {count} times. Set replace_all=true or provide more context to make it unique.",
            })

        new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
        full_path.write_text(new_content, encoding="utf-8")

        return json.dumps({
            "success": True,
            "replacements": count if replace_all else 1,
            "path": str(full_path),
        })

    return ToolSchema(
        name="edit_file",
        description=(
            "Edit a file by replacing an exact string match. "
            "Provide enough context in old_string to make the match unique. "
            "Use replace_all=true to replace all occurrences."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "old_string": {"type": "string", "description": "Exact string to find"},
                "new_string": {"type": "string", "description": "Replacement string"},
                "replace_all": {"type": "boolean", "description": "Replace all occurrences (default false)"},
            },
            "required": ["path", "old_string", "new_string"],
        },
        handler=handler,
    )


def build_list_files_tool(working_dir: str) -> ToolSchema:
    """Tool to list directory contents."""

    async def handler(params: dict[str, Any]) -> str:
        dir_path = params.get("path", ".")
        full_path = Path(working_dir) / dir_path if not os.path.isabs(dir_path) else Path(dir_path)

        if not full_path.exists():
            return json.dumps({"error": f"Directory not found: {dir_path}"})
        if not full_path.is_dir():
            return json.dumps({"error": f"Not a directory: {dir_path}"})

        recursive = params.get("recursive", False)
        max_entries = 200

        entries = []
        try:
            if recursive:
                for i, p in enumerate(sorted(full_path.rglob("*"))):
                    if i >= max_entries:
                        entries.append(f"... (truncated at {max_entries} entries)")
                        break
                    rel = p.relative_to(full_path)
                    suffix = "/" if p.is_dir() else ""
                    entries.append(f"{rel}{suffix}")
            else:
                for p in sorted(full_path.iterdir()):
                    suffix = "/" if p.is_dir() else ""
                    entries.append(f"{p.name}{suffix}")
        except Exception as exc:
            return json.dumps({"error": f"Cannot list directory: {exc}"})

        return json.dumps({"path": str(full_path), "entries": entries})

    return ToolSchema(
        name="list_files",
        description=(
            "List files and directories. Use recursive=true to see the full tree. "
            "Directories are shown with a trailing /."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Directory path (default: task root)"},
                "recursive": {"type": "boolean", "description": "List recursively (default false)"},
            },
        },
        handler=handler,
    )


def build_search_files_tool(working_dir: str) -> ToolSchema:
    """Tool to search file contents with grep-like functionality."""

    async def handler(params: dict[str, Any]) -> str:
        pattern = params.get("pattern", "")
        if not pattern:
            return json.dumps({"error": "No search pattern specified"})

        search_path = params.get("path", ".")
        full_path = Path(working_dir) / search_path if not os.path.isabs(search_path) else Path(search_path)
        include = params.get("file_pattern", "")

        import subprocess
        cmd = ["grep", "-rn", "--include", include, pattern, str(full_path)] if include else ["grep", "-rn", pattern, str(full_path)]

        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
                cwd=working_dir,
            )
            output = proc.stdout
            if len(output) > _MAX_READ:
                output = output[:_MAX_READ] + "\n... (truncated)"
            return json.dumps({
                "matches": output,
                "match_count": output.count("\n") if output else 0,
            })
        except subprocess.TimeoutExpired:
            return json.dumps({"error": "Search timed out"})
        except Exception as exc:
            return json.dumps({"error": f"Search failed: {exc}"})

    return ToolSchema(
        name="search_files",
        description=(
            "Search for a text pattern across files (like grep -rn). "
            "Returns matching lines with file paths and line numbers."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "Text or regex pattern to search for"},
                "path": {"type": "string", "description": "Directory to search in (default: task root)"},
                "file_pattern": {"type": "string", "description": "File glob pattern, e.g. '*.py'"},
            },
            "required": ["pattern"],
        },
        handler=handler,
    )


def build_all_file_tools(working_dir: str) -> list[ToolSchema]:
    """Build all file operation tools for a given working directory."""
    return [
        build_file_read_tool(working_dir),
        build_file_write_tool(working_dir),
        build_file_edit_tool(working_dir),
        build_list_files_tool(working_dir),
        build_search_files_tool(working_dir),
    ]
