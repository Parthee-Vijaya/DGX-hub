"""
Spark Hub — DGX Spark system monitor dashboard
FastAPI + Jinja2 + Tailwind. Port 7863.

Tre fokusområder:
  1. Detaljeret system-monitorering (CPU per-core, GPU, RAM, disk, net, processer)
  2. Restart-knapper for vLLM (port 8901) og LiteLLM (port 4000) user systemd units
  3. Indlejret terminal (iframe fra port 7862)
"""

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ---------------------------------------------------------------------------
# Sørg for at systemctl --user virker fra service-konteksten
# (dgx-3 har Linger=yes så /run/user/<uid> altid eksisterer)
# ---------------------------------------------------------------------------
os.environ.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")

CONFIG_PATH = Path.home() / "spark-hub-config.json"
DEFAULT_CONFIG = {
    "server_name": "Spark Hub",
    "tailscale_ip": "100.92.142.31",
    "terminal_port": 7862,
}

SERVICES = {
    "vllm": {
        "name": "vLLM",
        "unit": "vllm.service",
        "port": 8901,
        "health_path": "/v1/models",
        "description": "OpenAI-kompatibel inference server (Qwen3-Coder 30B FP8)",
    },
    "litellm": {
        "name": "LiteLLM",
        "unit": "litellm.service",
        "port": 4000,
        "health_path": "/health/liveliness",
        "description": "LLM-proxy der ruter til vLLM og Ollama",
    },
}


def load_config() -> dict:
    if CONFIG_PATH.exists():
        try:
            return {**DEFAULT_CONFIG, **json.loads(CONFIG_PATH.read_text())}
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(config: dict):
    CONFIG_PATH.write_text(json.dumps(config, indent=2, ensure_ascii=False))


app = FastAPI(title="Spark Hub")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")


# ---------------------------------------------------------------------------
# Sider
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    return RedirectResponse("/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "config": load_config(),
        "services": SERVICES,
    })


@app.get("/system", response_class=HTMLResponse)
async def system_page(request: Request):
    return templates.TemplateResponse("system.html", {
        "request": request,
        "config": load_config(),
    })


@app.get("/terminal", response_class=HTMLResponse)
async def terminal_page(request: Request):
    return templates.TemplateResponse("terminal.html", {
        "request": request,
        "config": load_config(),
    })


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "config": load_config(),
    })


@app.post("/settings")
async def settings_save(
    server_name: str = Form(...),
    tailscale_ip: str = Form(...),
    terminal_port: int = Form(7862),
):
    config = load_config()
    config["server_name"] = server_name
    config["tailscale_ip"] = tailscale_ip
    config["terminal_port"] = terminal_port
    save_config(config)
    return RedirectResponse("/settings?saved=1", status_code=303)


# ---------------------------------------------------------------------------
# Config / network
# ---------------------------------------------------------------------------

@app.get("/api/config")
async def api_config():
    return load_config()


@app.post("/api/config")
async def api_config_save(payload: dict):
    config = load_config()
    config.update(payload)
    save_config(config)
    return {"ok": True}


@app.get("/api/network")
async def api_network():
    hostname = _run("hostname")
    lan_ip = _run(
        "ip -4 addr show scope global | grep -v docker | grep -v br- | "
        "grep -oP '(?<=inet\\s)\\d+(\\.\\d+){3}' | head -1"
    )
    tailscale_ip = _run("tailscale ip -4 2>/dev/null | head -1")
    return {"hostname": hostname, "lan_ip": lan_ip, "tailscale_ip": tailscale_ip}


# ---------------------------------------------------------------------------
# Services: status + restart (user systemd)
# ---------------------------------------------------------------------------

def _systemctl_user(*args, timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["systemctl", "--user", *args],
        capture_output=True, text=True, timeout=timeout,
    )


async def _port_responsive(port: int, path: str = "/", timeout: float = 1.5) -> tuple[bool, int | None]:
    try:
        t0 = time.monotonic()
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.get(f"http://127.0.0.1:{port}{path}")
        return r.status_code < 500, round((time.monotonic() - t0) * 1000)
    except Exception:
        return False, None


@app.get("/api/services")
async def api_services():
    out = []
    for sid, svc in SERVICES.items():
        active = _systemctl_user("is-active", svc["unit"]).stdout.strip() or "inactive"
        responsive, latency_ms = await _port_responsive(svc["port"], svc["health_path"])
        out.append({
            "id": sid,
            "name": svc["name"],
            "unit": svc["unit"],
            "port": svc["port"],
            "description": svc["description"],
            "active": active,
            "responsive": responsive,
            "latency_ms": latency_ms,
        })
    return out


@app.get("/api/services/{sid}/status")
async def api_service_status(sid: str):
    svc = SERVICES.get(sid)
    if not svc:
        return JSONResponse({"error": "unknown service"}, status_code=404)
    active = _systemctl_user("is-active", svc["unit"]).stdout.strip() or "inactive"
    responsive, latency_ms = await _port_responsive(svc["port"], svc["health_path"])
    return {
        "id": sid, "name": svc["name"], "unit": svc["unit"], "port": svc["port"],
        "active": active, "responsive": responsive, "latency_ms": latency_ms,
    }


@app.post("/api/services/{sid}/restart")
async def api_service_restart(sid: str):
    svc = SERVICES.get(sid)
    if not svc:
        return JSONResponse({"error": "unknown service"}, status_code=404)
    r = _systemctl_user("restart", svc["unit"], timeout=30)
    return {
        "ok": r.returncode == 0,
        "returncode": r.returncode,
        "stdout": r.stdout.strip(),
        "stderr": r.stderr.strip(),
    }


@app.get("/api/services/{sid}/logs")
async def api_service_logs(sid: str, lines: int = 100):
    svc = SERVICES.get(sid)
    if not svc:
        return JSONResponse({"error": "unknown service"}, status_code=404)
    r = subprocess.run(
        ["journalctl", "--user", "-u", svc["unit"], "-n", str(min(max(lines, 10), 1000)),
         "--no-pager", "--output=short-iso"],
        capture_output=True, text=True, timeout=10,
    )
    return {"unit": svc["unit"], "lines": r.stdout.splitlines()}


@app.get("/api/services/{sid}/logs/stream")
async def api_service_logs_stream(sid: str, lines: int = 50):
    """Server-Sent Events stream af journalctl -f for en service."""
    svc = SERVICES.get(sid)
    if not svc:
        return JSONResponse({"error": "unknown service"}, status_code=404)

    async def event_stream():
        proc = await asyncio.create_subprocess_exec(
            "journalctl", "--user", "-u", svc["unit"],
            "-n", str(min(max(lines, 10), 500)),
            "-f", "--no-pager", "--output=short-iso",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                # SSE format: data: <line>\n\n
                text = line.decode("utf-8", errors="replace").rstrip("\n")
                # escape backslashes/newlines just in case
                yield f"data: {text}\n\n"
        finally:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=2)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# vLLM model switcher
# ---------------------------------------------------------------------------

VLLM_CONFIG_PATH = Path.home() / ".config" / "spark-hub" / "vllm-active.conf"
VLLM_MODELS = {
    "coder": {
        "name": "Qwen3-Coder 30B FP8",
        "description": "Kode-fokuseret, 65K kontekst, tool-calling",
        "huggingface": "Qwen/Qwen3-Coder-30B-A3B-Instruct-FP8",
        "context": 65536,
    },
    "omni": {
        "name": "Qwen3-Omni 30B",
        "description": "Multimodal (text/image/audio/video)",
        "huggingface": "Qwen/Qwen3-Omni-30B-A3B-Instruct",
        "context": 32768,
    },
    "gptoss": {
        "name": "GPT-OSS 120B",
        "description": "OpenAI open-weights, harmony chat template",
        "huggingface": "openai/gpt-oss-120b",
        "context": 8192,
    },
}


def _read_vllm_active() -> str:
    try:
        for line in VLLM_CONFIG_PATH.read_text().splitlines():
            line = line.strip()
            if line.startswith("VLLM_MODEL="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return "coder"


def _write_vllm_active(model: str):
    VLLM_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    VLLM_CONFIG_PATH.write_text(
        "# Aktiv vLLM model — Spark Hub bruger denne. Skift via dashboardet.\n"
        "# Tilladte: coder, omni, gptoss\n"
        f"VLLM_MODEL={model}\n"
    )


@app.get("/api/vllm/models")
async def api_vllm_models():
    active = _read_vllm_active()
    return {
        "active": active,
        "models": [
            {"id": mid, **meta, "active": mid == active}
            for mid, meta in VLLM_MODELS.items()
        ],
    }


@app.post("/api/vllm/switch")
async def api_vllm_switch(payload: dict):
    model = (payload or {}).get("model", "")
    if model not in VLLM_MODELS:
        return JSONResponse({"error": "unknown model"}, status_code=400)
    _write_vllm_active(model)
    # Restart vllm.service så wrapper plukker den nye config op
    r = _systemctl_user("restart", "vllm.service", timeout=30)
    return {
        "ok": r.returncode == 0,
        "active": model,
        "stderr": r.stderr.strip(),
    }


# ---------------------------------------------------------------------------
# System info
# ---------------------------------------------------------------------------

def _run(cmd: str, timeout: float = 4.0) -> str:
    try:
        return subprocess.check_output(cmd, shell=True, text=True, timeout=timeout).strip()
    except Exception:
        return ""


@app.get("/api/system")
async def api_system():
    loop = asyncio.get_event_loop()
    info = await loop.run_in_executor(None, _collect_system_info)
    return JSONResponse(info)


# Snapshots til delta-beregning på tværs af kald
_PREV_CPU_STATS: dict | None = None
_PREV_NET_STATS: dict | None = None
_PREV_DISK_STATS: dict | None = None
_PREV_TS: float = 0.0


def _read_proc_stat() -> dict[str, list[int]]:
    out = {}
    try:
        for line in Path("/proc/stat").read_text().splitlines():
            if not line.startswith("cpu"):
                continue
            parts = line.split()
            if len(parts) < 5:
                continue
            out[parts[0]] = [int(x) for x in parts[1:]]
    except Exception:
        pass
    return out


def _read_proc_net_dev() -> dict[str, tuple[int, int]]:
    out = {}
    try:
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]:
            name, _, rest = line.partition(":")
            cols = rest.split()
            if len(cols) >= 9:
                out[name.strip()] = (int(cols[0]), int(cols[8]))
    except Exception:
        pass
    return out


def _read_proc_diskstats() -> dict[str, tuple[int, int]]:
    out = {}
    try:
        for line in Path("/proc/diskstats").read_text().splitlines():
            parts = line.split()
            if len(parts) < 14:
                continue
            name = parts[2]
            if name.startswith(("loop", "ram", "dm-")):
                continue
            if any(ch.isdigit() for ch in name[-1:]) and not name.endswith(("0", "1")) and "p" in name:
                continue
            out[name] = (int(parts[5]), int(parts[9]))
    except Exception:
        pass
    return out


def _cpu_usage_from_delta(prev: list[int], curr: list[int]) -> float:
    if not prev or not curr or len(prev) < 4 or len(curr) < 4:
        return 0.0
    prev_idle = prev[3] + (prev[4] if len(prev) > 4 else 0)
    curr_idle = curr[3] + (curr[4] if len(curr) > 4 else 0)
    prev_total = sum(prev)
    curr_total = sum(curr)
    total_d = curr_total - prev_total
    idle_d = curr_idle - prev_idle
    if total_d <= 0:
        return 0.0
    return round((1 - idle_d / total_d) * 100, 1)


def _collect_system_info() -> dict:
    global _PREV_CPU_STATS, _PREV_NET_STATS, _PREV_DISK_STATS, _PREV_TS

    curr_cpu = _read_proc_stat()
    curr_net = _read_proc_net_dev()
    curr_disk = _read_proc_diskstats()
    now = time.monotonic()

    if _PREV_CPU_STATS is None:
        time.sleep(0.15)
        prev_cpu = curr_cpu
        curr_cpu = _read_proc_stat()
        prev_net = curr_net
        curr_net = _read_proc_net_dev()
        prev_disk = curr_disk
        curr_disk = _read_proc_diskstats()
        dt = 0.15
    else:
        prev_cpu = _PREV_CPU_STATS
        prev_net = _PREV_NET_STATS or curr_net
        prev_disk = _PREV_DISK_STATS or curr_disk
        dt = max(now - _PREV_TS, 0.05)

    _PREV_CPU_STATS = curr_cpu
    _PREV_NET_STATS = curr_net
    _PREV_DISK_STATS = curr_disk
    _PREV_TS = now

    # --- CPU total + per-core ---
    cpu_total = _cpu_usage_from_delta(prev_cpu.get("cpu", []), curr_cpu.get("cpu", []))
    per_core = []
    i = 0
    while True:
        key = f"cpu{i}"
        if key not in curr_cpu:
            break
        per_core.append(_cpu_usage_from_delta(prev_cpu.get(key, []), curr_cpu[key]))
        i += 1

    cpu_model = _run("lscpu | grep -m1 'Model name' | cut -d: -f2").strip()
    cpu_cores = _run("nproc")
    load_avg = _run("cat /proc/loadavg").split()[:3]

    # --- Memory ---
    mem = {}
    for line in _run("cat /proc/meminfo").splitlines():
        parts = line.split()
        if len(parts) >= 2:
            mem[parts[0].rstrip(":")] = int(parts[1])
    mem_total_gb = round(mem.get("MemTotal", 0) / 1048576, 1)
    mem_avail_gb = round(mem.get("MemAvailable", 0) / 1048576, 1)
    mem_used_gb = round(mem_total_gb - mem_avail_gb, 1)
    swap_total_gb = round(mem.get("SwapTotal", 0) / 1048576, 1)
    swap_free_gb = round(mem.get("SwapFree", 0) / 1048576, 1)
    swap_used_gb = round(swap_total_gb - swap_free_gb, 1)

    # --- GPU ---
    gpu_query = _run(
        "nvidia-smi --query-gpu="
        "name,temperature.gpu,power.draw,driver_version,"
        "memory.used,memory.total,utilization.gpu,utilization.memory,fan.speed,clocks.gr,clocks.mem "
        "--format=csv,noheader,nounits"
    )
    gpu = {}
    if gpu_query:
        vals = [v.strip() for v in gpu_query.split(",")]
        if len(vals) >= 7:
            gpu = {
                "name": vals[0],
                "temp_c": _safe_int(vals[1]),
                "power_w": vals[2],
                "driver": vals[3],
                "memory_used_mb": _safe_int(vals[4]),
                "memory_total_mb": _safe_int(vals[5]) or 131072,
                "util_gpu_pct": _safe_int(vals[6]),
                "util_mem_pct": _safe_int(vals[7]) if len(vals) > 7 else None,
                "fan_pct": _safe_int(vals[8]) if len(vals) > 8 else None,
                "clock_gr_mhz": _safe_int(vals[9]) if len(vals) > 9 else None,
                "clock_mem_mhz": _safe_int(vals[10]) if len(vals) > 10 else None,
            }
    if not gpu:
        gpu = {"name": "", "temp_c": None, "power_w": "", "driver": "",
               "memory_used_mb": 0, "memory_total_mb": 131072,
               "util_gpu_pct": None, "util_mem_pct": None,
               "fan_pct": None, "clock_gr_mhz": None, "clock_mem_mhz": None}

    # --- Temperaturer ---
    temps = []
    for zone_dir in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        try:
            t = int((zone_dir / "temp").read_text().strip()) / 1000
            ztype = (zone_dir / "type").read_text().strip()
            temps.append({"zone": ztype, "temp_c": round(t, 1)})
        except Exception:
            continue

    # --- Disk ---
    disks = []
    for line in _run(
        "df -h --output=source,fstype,size,used,avail,pcent,target "
        "-x tmpfs -x devtmpfs -x squashfs -x overlay"
    ).splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 7:
            disks.append({
                "source": parts[0],
                "fstype": parts[1],
                "size": parts[2],
                "used": parts[3],
                "avail": parts[4],
                "usage_pct": parts[5],
                "mount": " ".join(parts[6:]),
            })

    root_disk = next((d for d in disks if d["mount"] == "/"), disks[0] if disks else None) or {
        "size": "?", "used": "?", "avail": "?", "usage_pct": "0%",
    }

    # --- Disk I/O ---
    disk_io = []
    for name, (r_sec, w_sec) in curr_disk.items():
        pr_sec, pw_sec = prev_disk.get(name, (r_sec, w_sec))
        read_kbps = round((r_sec - pr_sec) * 512 / 1024 / dt, 1)
        write_kbps = round((w_sec - pw_sec) * 512 / 1024 / dt, 1)
        if read_kbps > 0 or write_kbps > 0 or name in ("nvme0n1", "sda", "sdb", "nvme1n1"):
            disk_io.append({"device": name, "read_kbps": read_kbps, "write_kbps": write_kbps})

    # --- Netværk ---
    interfaces = []
    for line in _run("ip -o addr show scope global").splitlines():
        parts = line.split()
        if len(parts) >= 4:
            iface = parts[1]
            addr = parts[3].split("/")[0]
            interfaces.append({"name": iface, "ip": addr})

    net_throughput = {}
    for iface, (rx, tx) in curr_net.items():
        prx, ptx = prev_net.get(iface, (rx, tx))
        net_throughput[iface] = {
            "rx_kbps": round((rx - prx) / 1024 / dt, 1),
            "tx_kbps": round((tx - ptx) / 1024 / dt, 1),
            "rx_total_mb": round(rx / 1048576, 1),
            "tx_total_mb": round(tx / 1048576, 1),
        }

    wifi_ssid = _run("nmcli -t -f active,ssid dev wifi 2>/dev/null | grep '^yes' | cut -d: -f2")
    wifi_signal = _run("nmcli -t -f active,signal dev wifi 2>/dev/null | grep '^yes' | cut -d: -f2")
    wifi_rate = _run("nmcli -t -f active,rate dev wifi 2>/dev/null | grep '^yes' | cut -d: -f2")

    top_cpu = _parse_ps(_run(
        "ps -eo pid,user,pcpu,pmem,rss,comm --sort=-pcpu --no-headers | head -10"
    ))
    top_mem = _parse_ps(_run(
        "ps -eo pid,user,pcpu,pmem,rss,comm --sort=-pmem --no-headers | head -10"
    ))

    display_raw = _run("DISPLAY=:0 xrandr --query 2>/dev/null | grep ' connected'") \
        or _run("xrandr --query 2>/dev/null | grep ' connected'")

    uptime_raw = _run("uptime -p")
    uptime_since = _run("uptime -s")
    hostname = _run("hostname")
    os_info = _run("lsb_release -d -s 2>/dev/null") or _run("grep PRETTY_NAME /etc/os-release | cut -d'\"' -f2")
    kernel = _run("uname -r")
    arch = _run("uname -m")

    containers = []
    for line in _run("docker ps --format '{{.Names}}|{{.Status}}|{{.Ports}}'").splitlines():
        parts = line.split("|")
        if len(parts) >= 2:
            containers.append({
                "name": parts[0],
                "status": parts[1],
                "ports": parts[2] if len(parts) > 2 else "",
            })

    services_state = {}
    for sid, svc in SERVICES.items():
        active = _systemctl_user("is-active", svc["unit"], timeout=3).stdout.strip() or "inactive"
        services_state[sid] = {"active": active, "port": svc["port"]}

    # --- GPU processer (nvidia-smi pmon) ---
    gpu_procs = []
    pmon_raw = _run("nvidia-smi pmon -c 1 -s um 2>/dev/null")
    pid_to_cmdline = {}
    for line in pmon_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        # gpu_idx pid type sm% mem% enc% dec% jpg% ofa% command
        if len(parts) < 4:
            continue
        try:
            pid = parts[1]
            proc_type = parts[2]
            sm_pct = parts[3] if parts[3] != "-" else None
            mem_pct = parts[4] if len(parts) > 4 and parts[4] != "-" else None
            command = parts[-1] if parts[-1] != "-" else ""
            gpu_procs.append({
                "pid": pid,
                "type": proc_type,
                "sm_pct": _safe_int(sm_pct) if sm_pct else None,
                "mem_pct": _safe_int(mem_pct) if mem_pct else None,
                "command": command,
            })
        except Exception:
            continue

    # --- GPU clocks / throttle / pstate ---
    gpu_extra = _run(
        "nvidia-smi --query-gpu=pstate,clocks_throttle_reasons.active "
        "--format=csv,noheader"
    )
    if gpu_extra:
        parts = [p.strip() for p in gpu_extra.split(",")]
        if len(parts) >= 2:
            gpu["pstate"] = parts[0]
            throttle_hex = parts[1]
            # 0x0 = ingen throttle; ellers afkod bit-flags
            gpu["throttle"] = _decode_throttle(throttle_hex)

    # --- NVMe info ---
    nvme_devices = _collect_nvme_info()

    return {
        "hostname": hostname,
        "os": os_info,
        "kernel": kernel,
        "arch": arch,
        "uptime": uptime_raw,
        "uptime_since": uptime_since,
        "cpu": {
            "model": cpu_model,
            "cores": int(cpu_cores) if cpu_cores else 0,
            "usage_pct": cpu_total,
            "per_core": per_core,
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
        "gpu": gpu,
        "temperatures": temps,
        "disks": disks,
        "disk": {
            "total": root_disk["size"],
            "used": root_disk["used"],
            "available": root_disk["avail"],
            "usage_pct": root_disk["usage_pct"],
        },
        "disk_io": disk_io,
        "network": {
            "interfaces": interfaces,
            "throughput": net_throughput,
            "wifi": {
                "ssid": wifi_ssid,
                "signal_pct": _safe_int(wifi_signal),
                "rate": wifi_rate,
            },
        },
        "top_cpu": top_cpu,
        "top_mem": top_mem,
        "display": display_raw or "Ingen skærm fundet",
        "containers": containers,
        "services": services_state,
        "gpu_procs": gpu_procs,
        "nvme": nvme_devices,
    }


def _decode_throttle(hex_str: str) -> list[str]:
    """Afkod clocks_throttle_reasons.active bit-flags."""
    try:
        bits = int(hex_str, 16)
    except Exception:
        return []
    reasons = []
    # Reference: https://docs.nvidia.com/deploy/nvml-api/group__nvmlClocksThrottleReasons.html
    flags = [
        (0x0000000000000001, "GpuIdle"),
        (0x0000000000000002, "Applications clocks setting"),
        (0x0000000000000004, "SW power cap"),
        (0x0000000000000008, "HW slowdown"),
        (0x0000000000000010, "Sync boost"),
        (0x0000000000000020, "SW thermal slowdown"),
        (0x0000000000000040, "HW thermal slowdown"),
        (0x0000000000000080, "HW power brake slowdown"),
        (0x0000000000000100, "Display clock setting"),
    ]
    for bit, name in flags:
        if bits & bit:
            reasons.append(name)
    return reasons


def _collect_nvme_info() -> list[dict]:
    """Læs NVMe-info uden sudo via /sys/class/nvme + nvme list."""
    devices = []

    # Per-NVMe via sysfs
    nvme_root = Path("/sys/class/nvme")
    if not nvme_root.exists():
        return devices

    # Brug 'nvme list' for kapacitet/usage (kører uden sudo for list-operation)
    usage_map = {}
    nvme_list_raw = _run("nvme list 2>/dev/null")
    for line in nvme_list_raw.splitlines():
        # Format: /dev/nvme0n1 ... Usage Format FW
        if line.startswith("/dev/nvme"):
            parts = line.split()
            if len(parts) >= 2:
                # Find usage: typisk "675.47  GB /   4.10  TB"
                # Vi finder "GB" eller "TB" og parse derudfra
                joined = " ".join(parts)
                usage_match = None
                for unit in ["GB", "TB"]:
                    if unit in joined:
                        try:
                            # Tag de første 4-5 tokens efter device-navn
                            after_dev = parts[2:]  # spring /dev/nvmeXnY og /dev/ngXnY over
                            # Find "X.XX GB / Y.YY TB" - rekonstruér
                            usage_match = " ".join(after_dev[3:9]) if len(after_dev) > 8 else joined
                        except Exception:
                            usage_match = ""
                        break
                usage_map[parts[0]] = {
                    "raw_line": " ".join(parts),
                }

    for ctrl_dir in sorted(nvme_root.glob("nvme*")):
        # Spring namespaces over (nvme0n1) — kun controllers (nvme0)
        if not ctrl_dir.name.startswith("nvme") or "n" in ctrl_dir.name[4:]:
            continue
        try:
            model = (ctrl_dir / "model").read_text().strip()
            serial = (ctrl_dir / "serial").read_text().strip()
            firmware = (ctrl_dir / "firmware_rev").read_text().strip()
            state = (ctrl_dir / "state").read_text().strip()
        except Exception:
            continue

        # Temperaturer fra hwmon
        temps = []
        for hwmon in sorted(ctrl_dir.glob("hwmon*")):
            for temp_input in sorted(hwmon.glob("temp*_input")):
                try:
                    n = temp_input.name.replace("_input", "")
                    label_path = hwmon / f"{n}_label"
                    label = label_path.read_text().strip() if label_path.exists() else n
                    temp_mc = int(temp_input.read_text().strip())
                    temps.append({"label": label, "temp_c": round(temp_mc / 1000, 1)})
                except Exception:
                    continue

        # Find tilhørende namespace + størrelse
        namespace = None
        size_str = None
        for ns_dir in sorted(ctrl_dir.glob(f"{ctrl_dir.name}n*")):
            namespace = ns_dir.name
            size_sec_path = ns_dir / "size"
            if size_sec_path.exists():
                try:
                    sectors = int(size_sec_path.read_text().strip())
                    # NVMe sektor 512 bytes
                    size_str = f"{sectors * 512 / 1e12:.2f} TB"
                except Exception:
                    pass
            break

        devices.append({
            "device": ctrl_dir.name,
            "namespace": namespace,
            "size": size_str or "?",
            "model": model,
            "serial": serial,
            "firmware": firmware,
            "state": state,
            "temperatures": temps,
        })

    return devices


def _parse_ps(raw: str) -> list[dict]:
    rows = []
    for line in raw.splitlines():
        parts = line.split(None, 5)
        if len(parts) < 6:
            continue
        rows.append({
            "pid": parts[0],
            "user": parts[1],
            "cpu_pct": _safe_float(parts[2]),
            "mem_pct": _safe_float(parts[3]),
            "rss_kb": _safe_int(parts[4]),
            "command": parts[5][:60],
        })
    return rows


def _safe_int(v) -> int | None:
    try:
        return int(float(v))
    except Exception:
        return None


def _safe_float(v) -> float | None:
    try:
        return float(v)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Speedtest (speedtest-cli)
# ---------------------------------------------------------------------------

_speedtest_state: dict = {
    "status": "idle",  # idle | running | done | error
    "started_at": None,
    "finished_at": None,
    "result": None,
    "error": None,
}
_speedtest_lock = asyncio.Lock()


async def _run_speedtest():
    """Kør speedtest-cli og gem resultatet i _speedtest_state."""
    import json as _json
    global _speedtest_state
    _speedtest_state.update({
        "status": "running",
        "started_at": time.time(),
        "finished_at": None,
        "result": None,
        "error": None,
    })
    try:
        proc = await asyncio.create_subprocess_exec(
            str(Path(__file__).parent / "venv" / "bin" / "speedtest-cli"),
            "--json", "--secure",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
        if proc.returncode != 0:
            raise RuntimeError(stderr.decode("utf-8", "replace")[:500])
        data = _json.loads(stdout.decode("utf-8", "replace"))
        _speedtest_state.update({
            "status": "done",
            "finished_at": time.time(),
            "result": {
                "download_mbps": round(data["download"] / 1e6, 2),
                "upload_mbps": round(data["upload"] / 1e6, 2),
                "ping_ms": round(data["ping"], 1),
                "server_name": data.get("server", {}).get("name", ""),
                "server_country": data.get("server", {}).get("country", ""),
                "server_sponsor": data.get("server", {}).get("sponsor", ""),
                "bytes_sent": data.get("bytes_sent"),
                "bytes_received": data.get("bytes_received"),
                "timestamp": data.get("timestamp"),
            },
        })
    except Exception as e:
        _speedtest_state.update({
            "status": "error",
            "finished_at": time.time(),
            "error": str(e)[:500],
        })


@app.post("/api/speedtest/run")
async def api_speedtest_run():
    async with _speedtest_lock:
        if _speedtest_state["status"] == "running":
            return JSONResponse({"error": "already running"}, status_code=409)
        asyncio.create_task(_run_speedtest())
    return {"ok": True, "status": "running"}


@app.get("/api/speedtest/status")
async def api_speedtest_status():
    return _speedtest_state


# ---------------------------------------------------------------------------
# Power (reboot / shutdown) — kræver polkit-rule for at virke uden adgangskode
# ---------------------------------------------------------------------------

@app.post("/api/power/{action}")
async def api_power(action: str):
    if action not in ("reboot", "poweroff"):
        return JSONResponse({"error": "invalid action"}, status_code=400)
    r = subprocess.run(
        ["systemctl", action],
        capture_output=True, text=True, timeout=10,
    )
    return {"ok": r.returncode == 0, "stderr": r.stderr}


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7863)
