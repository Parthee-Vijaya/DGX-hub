"""
Spark Hub — Hjemmeserver portal til DGX Spark
FastAPI backend med Jinja2 templates og Tailwind CSS
Port 7863
"""

import json
import asyncio
import subprocess
import time
from pathlib import Path
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_PATH = Path.home() / "spark-hub-config.json"

TAILSCALE_IP = "100.92.142.31"

DEFAULT_CONFIG = {
    "setup_done": False,
    "server_name": "Spark Hub",
    "tailscale_ip": TAILSCALE_IP,
    "media_paths": {
        "film": str(Path.home() / "Media/film"),
        "serier": str(Path.home() / "Media/serier"),
    },
}

SERVICES = [
    {
        "id": "jellyfin",
        "name": "Jellyfin",
        "description": "Open source medieserver",
        "subtitle": "Stream film, serier og musik til alle enheder",
        "icon": "film",
        "port": 8096,
        "url_template": "http://{ip}:8096",
        "health_url": "http://localhost:8096/health",
        "color": "from-purple-500 to-purple-700",
        "category": "media",
    },
    {
        "id": "plex",
        "name": "Plex",
        "description": "Premium medieserver",
        "subtitle": "Film, serier og live TV med apps til alle platforme",
        "icon": "tv",
        "port": 32400,
        "url_template": "http://{ip}:32400/web",
        "health_url": "http://localhost:32400/web",
        "color": "from-yellow-500 to-orange-600",
        "category": "media",
    },
    {
        "id": "immich",
        "name": "Immich",
        "description": "Foto & video backup",
        "subtitle": "Automatisk backup fra mobil med AI-genkendelse",
        "icon": "camera",
        "port": 2283,
        "url_template": "http://{ip}:2283",
        "health_url": "http://localhost:2283/api/server/ping",
        "color": "from-green-500 to-teal-600",
        "category": "media",
    },
    {
        "id": "ai_toolbox",
        "name": "AI Toolbox",
        "description": "Multimodal AI suite",
        "subtitle": "Qwen3-Omni chat + billedgenerering via vLLM",
        "icon": "cpu",
        "port": 7860,
        "url_template": "http://{ip}:7860",
        "health_url": "http://localhost:7860/",
        "color": "from-blue-500 to-indigo-600",
        "category": "ai",
    },
    {
        "id": "ollama",
        "name": "Ollama",
        "description": "LLM model server",
        "subtitle": "Gemma4, Qwen2.5-Coder og flere lokale modeller",
        "icon": "brain",
        "port": 11434,
        "url_template": "http://{ip}:11434",
        "health_url": "http://localhost:11434/api/tags",
        "color": "from-red-500 to-pink-600",
        "category": "ai",
    },
    {
        "id": "nextcloud",
        "name": "Nextcloud",
        "description": "Privat cloud",
        "subtitle": "Filer, kalender, kontakter og samarbejde",
        "icon": "cloud",
        "port": 8080,
        "url_template": "http://{ip}:8080",
        "health_url": "http://localhost:8080/status.php",
        "color": "from-blue-400 to-cyan-600",
        "category": "media",
    },
]


def load_config() -> dict:
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            data = json.load(f)
        merged = {**DEFAULT_CONFIG, **data}
        return merged
    return DEFAULT_CONFIG.copy()


def save_config(config: dict):
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Pre-load Gemma4 i Ollama ved opstart
    asyncio.create_task(_preload_model())
    yield


async def _preload_model():
    """Varm Ollama op ved at sende en kort request så modellen loades i GPU."""
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            await client.post(
                "http://localhost:11434/api/chat",
                json={"model": "mistral-small3.2:latest", "messages": [{"role": "user", "content": "hi"}], "stream": False},
            )
    except Exception:
        pass


app = FastAPI(title="Spark Hub", lifespan=lifespan)
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


# ---------------------------------------------------------------------------
# Health check helper
# ---------------------------------------------------------------------------

async def check_service(client: httpx.AsyncClient, service: dict, request_host: str | None = None) -> dict:
    response_ms = None
    try:
        t0 = time.monotonic()
        resp = await client.get(service["health_url"], timeout=3.0, follow_redirects=True)
        response_ms = round((time.monotonic() - t0) * 1000)
        online = resp.status_code < 500
    except Exception:
        online = False
    # Brug browserens hostname/IP i stedet for hardcoded IP
    if request_host:
        ip = request_host.split(":")[0]  # fjern port
    else:
        config = load_config()
        ip = config.get("tailscale_ip", TAILSCALE_IP)
    return {
        "id": service["id"],
        "name": service["name"],
        "online": online,
        "response_ms": response_ms,
        "url": service["url_template"].format(ip=ip),
        "icon": service["icon"],
        "description": service["description"],
        "subtitle": service.get("subtitle", ""),
        "port": service.get("port"),
        "color": service["color"],
        "category": service["category"],
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    config = load_config()
    if not config.get("setup_done"):
        return RedirectResponse("/wizard")
    return RedirectResponse("/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    config = load_config()
    host = request.headers.get("host", "")
    ip = host.split(":")[0] if host else config.get("tailscale_ip", TAILSCALE_IP)
    services_with_urls = [
        {**s, "url": s["url_template"].format(ip=ip)}
        for s in SERVICES
    ]
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "config": config,
        "services": services_with_urls,
    })


@app.get("/wizard", response_class=HTMLResponse)
async def wizard(request: Request):
    config = load_config()
    return templates.TemplateResponse("wizard.html", {
        "request": request,
        "config": config,
    })


@app.post("/api/setup/complete")
async def setup_complete(
    server_name: str = Form("Spark Hub"),
    tailscale_ip: str = Form(TAILSCALE_IP),
):
    config = load_config()
    config["setup_done"] = True
    config["server_name"] = server_name
    config["tailscale_ip"] = tailscale_ip
    save_config(config)
    return RedirectResponse("/dashboard", status_code=303)


@app.get("/api/status")
async def api_status(request: Request):
    host = request.headers.get("host")
    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[check_service(client, s, request_host=host) for s in SERVICES]
        )
    return JSONResponse({"services": list(results)})


@app.get("/api/config")
async def api_config():
    return JSONResponse(load_config())


@app.get("/api/network")
async def api_network():
    """Returnér maskinens aktuelle IP-adresser."""
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(None, _get_network_info)
    return JSONResponse(info)


def _get_network_info() -> dict:
    lan = _run("ip -4 addr show scope global | grep -v docker | grep -v br- | grep -v tailscale | grep -oP '(?<=inet\\s)\\d+(\\.\\d+){3}' | head -1")
    ts = _run("tailscale ip -4 2>/dev/null")
    hostname = _run("hostname")
    return {"lan_ip": lan, "tailscale_ip": ts, "hostname": hostname, "port": 7863}


@app.post("/api/config")
async def api_config_update(request: Request):
    body = await request.json()
    config = load_config()
    config.update(body)
    save_config(config)
    return JSONResponse({"ok": True})


@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    config = load_config()
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "config": config,
    })


@app.post("/settings")
async def settings_save(
    request: Request,
    server_name: str = Form(...),
    tailscale_ip: str = Form(...),
):
    config = load_config()
    config["server_name"] = server_name
    config["tailscale_ip"] = tailscale_ip
    save_config(config)
    return RedirectResponse("/settings?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# System status API
# ---------------------------------------------------------------------------

def _run(cmd: str) -> str:
    try:
        return subprocess.check_output(cmd, shell=True, text=True, timeout=5).strip()
    except Exception:
        return ""


@app.get("/system", response_class=HTMLResponse)
async def system_page(request: Request):
    config = load_config()
    return templates.TemplateResponse("system.html", {
        "request": request,
        "config": config,
    })


@app.get("/api/system")
async def api_system():
    """Samler alt system-info i ét JSON endpoint."""
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(None, _collect_system_info)
    return JSONResponse(info)


def _collect_system_info() -> dict:
    # --- CPU ---
    cpu_model = _run("lscpu | grep 'Model name' | head -1 | cut -d: -f2").strip()
    cpu_model2 = _run("lscpu | grep 'Model name' | tail -1 | cut -d: -f2").strip()
    cpu_cores = _run("nproc")
    load_avg = _run("cat /proc/loadavg").split()[:3]

    # CPU usage per core fra /proc/stat
    cpu_usage = _run(
        "top -bn1 | grep '%Cpu' | head -1 | awk '{print $2}'"
    )

    # --- Memory ---
    mem = {}
    for line in _run("cat /proc/meminfo").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            key = parts[0].rstrip(":")
            mem[key] = int(parts[1])  # kB
    mem_total_gb = round(mem.get("MemTotal", 0) / 1048576, 1)
    mem_avail_gb = round(mem.get("MemAvailable", 0) / 1048576, 1)
    mem_used_gb = round(mem_total_gb - mem_avail_gb, 1)
    swap_total_gb = round(mem.get("SwapTotal", 0) / 1048576, 1)
    swap_free_gb = round(mem.get("SwapFree", 0) / 1048576, 1)
    swap_used_gb = round(swap_total_gb - swap_free_gb, 1)

    # --- GPU ---
    gpu_name = _run(
        "nvidia-smi --query-gpu=name --format=csv,noheader"
    ).strip()
    gpu_temp = _run(
        "nvidia-smi --query-gpu=temperature.gpu --format=csv,noheader"
    ).strip()
    gpu_power = _run(
        "nvidia-smi --query-gpu=power.draw --format=csv,noheader"
    ).strip()
    driver_version = _run(
        "nvidia-smi --query-gpu=driver_version --format=csv,noheader"
    ).strip()

    # --- Temperatures ---
    temps = []
    thermal_zones = _run("ls /sys/class/thermal/ 2>/dev/null | grep thermal_zone").split()
    for zone in thermal_zones[:8]:
        temp_raw = _run(f"cat /sys/class/thermal/{zone}/temp 2>/dev/null")
        zone_type = _run(f"cat /sys/class/thermal/{zone}/type 2>/dev/null")
        if temp_raw:
            temps.append({
                "zone": zone_type or zone,
                "temp_c": round(int(temp_raw) / 1000, 1),
            })

    # --- Disk ---
    disk_info = _run("df -h / | tail -1").split()
    disk_total = disk_info[1] if len(disk_info) > 1 else "?"
    disk_used = disk_info[2] if len(disk_info) > 2 else "?"
    disk_avail = disk_info[3] if len(disk_info) > 3 else "?"
    disk_pct = disk_info[4] if len(disk_info) > 4 else "?"

    # --- Network ---
    interfaces = []
    for line in _run("ip -o addr show scope global").splitlines():
        parts = line.split()
        if len(parts) >= 4:
            iface = parts[1]
            addr = parts[3].split("/")[0]
            interfaces.append({"name": iface, "ip": addr})

    wifi_ssid = _run("nmcli -t -f active,ssid dev wifi | grep '^yes' | cut -d: -f2")
    wifi_signal = _run("nmcli -t -f active,signal dev wifi | grep '^yes' | cut -d: -f2")
    wifi_rate = _run("nmcli -t -f active,rate dev wifi | grep '^yes' | cut -d: -f2")

    # --- Display ---
    display_raw = _run("DISPLAY=:0 xrandr --query 2>/dev/null | grep ' connected'")
    display_info = display_raw if display_raw else _run("xrandr --query 2>/dev/null | grep ' connected'")

    # --- Uptime ---
    uptime_raw = _run("uptime -p")
    uptime_since = _run("uptime -s")

    # --- Hostname & OS ---
    hostname = _run("hostname")
    os_info = _run("lsb_release -d -s 2>/dev/null") or _run("cat /etc/os-release | grep PRETTY_NAME | cut -d'\"' -f2")
    kernel = _run("uname -r")
    arch = _run("uname -m")

    # --- Docker containers ---
    containers = []
    for line in _run("docker ps --format '{{.Names}}|{{.Status}}|{{.Ports}}'").splitlines():
        parts = line.split("|")
        if len(parts) >= 2:
            containers.append({
                "name": parts[0],
                "status": parts[1],
                "ports": parts[2] if len(parts) > 2 else "",
            })

    return {
        "hostname": hostname,
        "os": os_info,
        "kernel": kernel,
        "arch": arch,
        "uptime": uptime_raw,
        "uptime_since": uptime_since,
        "cpu": {
            "models": [m for m in [cpu_model, cpu_model2] if m],
            "cores": int(cpu_cores) if cpu_cores else 0,
            "usage_pct": float(cpu_usage) if cpu_usage else 0,
            "load_avg": load_avg,
        },
        "memory": {
            "total_gb": mem_total_gb,
            "used_gb": mem_used_gb,
            "available_gb": mem_avail_gb,
            "usage_pct": round(mem_used_gb / mem_total_gb * 100, 1) if mem_total_gb > 0 else 0,
            "swap_total_gb": swap_total_gb,
            "swap_used_gb": swap_used_gb,
        },
        "gpu": {
            "name": gpu_name,
            "temp_c": int(gpu_temp) if gpu_temp.isdigit() else None,
            "power_w": gpu_power,
            "driver": driver_version,
            "memory_total_gb": 128,
        },
        "temperatures": temps,
        "disk": {
            "total": disk_total,
            "used": disk_used,
            "available": disk_avail,
            "usage_pct": disk_pct,
        },
        "network": {
            "interfaces": interfaces,
            "wifi": {
                "ssid": wifi_ssid,
                "signal_pct": int(wifi_signal) if wifi_signal.isdigit() else None,
                "rate": wifi_rate,
            },
        },
        "display": display_info or "Ingen skærm fundet",
        "containers": containers,
    }


# ---------------------------------------------------------------------------
# Chat assistant (Ollama / gemma4)
# ---------------------------------------------------------------------------

OLLAMA_URL = "http://localhost:11434"
DEFAULT_CHAT_MODEL = "mistral-small3.2:latest"
CHAT_SYSTEM_PROMPT = (
    "Du er en hjælpsom assistent på en DGX Spark hjemmeserver. "
    "Svar på det sprog brugeren skriver på. "
    "Vær kort og præcis."
)


@app.get("/chat", response_class=HTMLResponse)
async def chat_page(request: Request):
    config = load_config()
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags", timeout=3.0)
            models = [m["name"] for m in r.json().get("models", [])]
    except Exception:
        models = [DEFAULT_CHAT_MODEL]
    return templates.TemplateResponse("chat.html", {
        "request": request,
        "config": config,
        "models": models,
        "default_model": DEFAULT_CHAT_MODEL if DEFAULT_CHAT_MODEL in models else (models[0] if models else ""),
    })


@app.post("/api/chat/stream")
async def chat_stream(request: Request):
    """Stream svar fra Ollama som SSE, inklusive metrics i sidste event."""
    body = await request.json()
    messages = body.get("messages", [])
    model = body.get("model", DEFAULT_CHAT_MODEL)

    if not messages or messages[0].get("role") != "system":
        messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}] + messages

    async def event_generator():
        first_token_time = None
        start_time = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    f"{OLLAMA_URL}/api/chat",
                    json={"model": model, "messages": messages, "stream": True},
                ) as resp:
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                            content = data.get("message", {}).get("content", "")
                            done = data.get("done", False)

                            if content and first_token_time is None:
                                first_token_time = time.monotonic()

                            payload = {"content": content, "done": done}

                            if done:
                                # Ollama sender metrics i det sidste svar
                                elapsed = time.monotonic() - start_time
                                ttft = (first_token_time - start_time) if first_token_time else 0
                                eval_count = data.get("eval_count", 0)
                                eval_duration = data.get("eval_duration", 0)  # nanoseconds
                                prompt_eval_count = data.get("prompt_eval_count", 0)
                                prompt_eval_duration = data.get("prompt_eval_duration", 0)

                                tokens_per_sec = (
                                    eval_count / (eval_duration / 1e9)
                                    if eval_duration > 0 else 0
                                )
                                prompt_tps = (
                                    prompt_eval_count / (prompt_eval_duration / 1e9)
                                    if prompt_eval_duration > 0 else 0
                                )

                                payload["metrics"] = {
                                    "model": model,
                                    "total_tokens": eval_count + prompt_eval_count,
                                    "generated_tokens": eval_count,
                                    "prompt_tokens": prompt_eval_count,
                                    "tokens_per_sec": round(tokens_per_sec, 1),
                                    "prompt_tps": round(prompt_tps, 1),
                                    "time_to_first_token_ms": round(ttft * 1000),
                                    "total_time_sec": round(elapsed, 2),
                                }

                            yield f"data: {json.dumps(payload)}\n\n"
                            if done:
                                break
                        except json.JSONDecodeError:
                            continue
        except Exception as e:
            yield f"data: {json.dumps({'content': '', 'done': True, 'error': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@app.get("/api/chat/models")
async def chat_models():
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(f"{OLLAMA_URL}/api/tags", timeout=3.0)
            raw = r.json().get("models", [])
            models = []
            for m in raw:
                size_bytes = m.get("size", 0)
                size_gb = round(size_bytes / 1e9, 1) if size_bytes else None
                params = m.get("details", {}).get("parameter_size", "")
                family = m.get("details", {}).get("family", "")
                quant = m.get("details", {}).get("quantization_level", "")
                models.append({
                    "name": m["name"],
                    "size_gb": size_gb,
                    "params": params,
                    "family": family,
                    "quant": quant,
                })
        return JSONResponse({"models": models})
    except Exception:
        return JSONResponse({"models": []})


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=7863, reload=False)
