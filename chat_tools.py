"""Tool definitions and executors for the chat assistant.

The chat backend sends ``TOOL_DEFINITIONS`` to Ollama. When the model
decides to call a tool, ``execute_tool`` runs it and returns a JSON-
serialisable result that is fed back into the conversation.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable

# JSON-schema definitions sent to Ollama as the ``tools`` parameter.
TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "get_system_status",
            "description": (
                "Get current server status: CPU usage, RAM, GPU temp, disk, "
                "uptime, number of running containers. Use when user asks "
                "about hardware, performance, server health."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_services",
            "description": (
                "List all hub services (Jellyfin, Plex, Immich, Nextcloud, "
                "Ollama) with online/offline status and access URLs. Use when "
                "user asks what is running, what is installed, what URLs to use."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_service",
            "description": (
                "Restart a single service. ONLY call this when the user "
                "explicitly asks to restart something or when a service is "
                "confirmed offline and the user wants it fixed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": ["jellyfin", "plex", "immich", "nextcloud", "ollama"],
                    }
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_media",
            "description": (
                "Search for files in the user's Media folder by filename keyword. "
                "Returns matching paths grouped by category (film, serier, fotos, "
                "musik, dokumenter, boger). Use when user asks 'do I have...', "
                "'find...', 'where is...'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Substring to search for in filenames (case-insensitive)",
                    },
                    "category": {
                        "type": "string",
                        "enum": ["film", "serier", "fotos", "musik", "dokumenter", "boger", "all"],
                        "description": "Limit search to one category, or 'all' for everything",
                    },
                },
                "required": ["query"],
            },
        },
    },
]


async def execute_tool(
    name: str,
    args: dict,
    *,
    collect_system_info: Callable[[], dict],
    installer_registry: dict,
    load_config: Callable[[], dict],
) -> dict:
    """Run a tool by name. Returns a JSON-serialisable dict."""
    args = args or {}

    if name == "get_system_status":
        loop = asyncio.get_event_loop()
        info = await loop.run_in_executor(None, collect_system_info)
        return {
            "uptime": info.get("uptime"),
            "cpu_usage_pct": info["cpu"]["usage_pct"],
            "memory": {
                "used_gb": info["memory"]["used_gb"],
                "total_gb": info["memory"]["total_gb"],
                "usage_pct": info["memory"]["usage_pct"],
            },
            "gpu": {
                "name": info["gpu"]["name"],
                "temp_c": info["gpu"].get("temp_c"),
                "power": info["gpu"].get("power_w"),
            },
            "disk": {
                "used": info["disk"]["used"],
                "total": info["disk"]["total"],
                "usage_pct": info["disk"]["usage_pct"],
            },
            "containers_running": len(info.get("containers", [])),
        }

    if name == "list_services":
        results = []
        for sid, inst in installer_registry.items():
            try:
                d = await inst.detect()
            except Exception as e:
                d = {"installed": False, "running": False, "error": str(e)}
            results.append({
                "id": sid,
                "name": inst.name,
                "online": bool(d.get("running")),
                "installed": bool(d.get("installed")),
                "port": inst.port,
                "url": f"http://localhost:{inst.port}",
            })
        return {"services": results}

    if name == "restart_service":
        sname = args.get("name", "").lower()
        inst = installer_registry.get(sname)
        if not inst:
            return {"ok": False, "error": f"ukendt service: {sname}"}
        rc = 0
        log_lines: list[str] = []
        try:
            async for line in inst.restart():
                if line.startswith("__exit__:"):
                    rc = int(line.split(":", 1)[1])
                else:
                    log_lines.append(line)
        except Exception as e:
            return {"ok": False, "error": str(e), "service": sname}
        return {
            "ok": rc == 0,
            "service": sname,
            "log_summary": " | ".join(log_lines[-3:]) if log_lines else "",
        }

    if name == "search_media":
        cfg = load_config()
        media_root = Path(cfg.get("media_root", str(Path.home() / "Media")))
        query = (args.get("query") or "").lower().strip()
        category = args.get("category", "all")

        if not query:
            return {"matches": [], "count": 0, "note": "tomt søgeord"}
        if not media_root.exists():
            return {"matches": [], "count": 0, "note": f"media_root findes ikke: {media_root}"}

        roots = [media_root] if category == "all" else [media_root / category]
        results: list[dict] = []
        max_results = 25
        for root in roots:
            if not root.exists():
                continue
            try:
                for p in root.rglob("*"):
                    if not p.is_file():
                        continue
                    if query not in p.name.lower():
                        continue
                    try:
                        size = p.stat().st_size
                    except OSError:
                        continue
                    rel = p.relative_to(media_root)
                    results.append({
                        "filename": p.name,
                        "path": str(rel),
                        "category": rel.parts[0] if rel.parts else "",
                        "size_mb": round(size / 1048576, 2),
                    })
                    if len(results) >= max_results:
                        break
                if len(results) >= max_results:
                    break
            except PermissionError:
                continue
        return {"matches": results, "count": len(results), "truncated": len(results) >= max_results}

    return {"error": f"ukendt tool: {name}"}
