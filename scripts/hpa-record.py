#!/usr/bin/env python3
"""Grava o teste de carga com HPA (roteiro em docs/devops/load-testing.md).

Roda os três "terminais" do roteiro e grava as saídas lado a lado, sem depender
de janela aberta: uma página local mostra o que cada comando imprime e um Edge
sem janela tira 2 quadros por segundo. Nada da tela de quem roda entra no vídeo.

    terminal 1   kubectl autoscale deployment gateway --cpu=50% --min=2 --max=6
                 kubectl get hpa gateway -w
    terminal 2   kubectl get pods -l app=gateway -o wide -w
    terminal 3   ./scripts/load-test.sh  (k6 no cenário auth)

Depois do k6, continua gravando até o HPA voltar para 2 réplicas (≈ 5 min) e
gera: vídeo em tempo real e acelerado, linha do tempo das réplicas (CSV), resumo
(JSON) e prints do Grafana com o intervalo exato do teste.

Uso:
    python scripts/hpa-record.py                          # ensaio local (scripts/kind-up.sh)
    python scripts/hpa-record.py --base-url http://<elb> --name eks-hpa --label "EKS (eks-store)"

O kubectl usa o contexto atual (kind: kind-store; EKS: aws eks update-kubeconfig).
Requisitos: Docker, kubectl, Git Bash (Windows) e
    pip install playwright imageio-ffmpeg pillow   (usa o Edge instalado: --browser msedge)
"""
from __future__ import annotations

import argparse
import base64
import csv
import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


# ----------------------------------------------------------------------------- utilidades
def find_bash() -> str:
    # no Windows, o "bash" do PATH pode ser o do WSL: preferir o Git Bash
    for c in (os.environ.get("GIT_BASH"), r"C:\Program Files\Git\bin\bash.exe",
              r"C:\Program Files (x86)\Git\bin\bash.exe", shutil.which("bash")):
        if c and Path(c).exists():
            return c
    sys.exit("Git Bash não encontrado (defina GIT_BASH)")


def wait_port(port: int, timeout: float = 30) -> None:
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.5)
    sys.exit(f"porta {port} não abriu (port-forward falhou?)")


def mmss(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


class Kube:
    def __init__(self, context: str | None, ns: str):
        self.base = ["kubectl"] + (["--context", context] if context else [])
        self.ns = ns

    def argv(self, *a: str) -> list[str]:
        return self.base + list(a) + ["-n", self.ns]

    def display(self, *a: str) -> str:
        return " ".join(self.argv(*a))

    def run(self, *a: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(self.argv(*a), capture_output=True, text=True,
                              encoding="utf-8", errors="replace", check=check)

    def json(self, *a: str):
        r = self.run(*a, "-o", "json", check=False)
        return json.loads(r.stdout) if r.returncode == 0 and r.stdout.strip() else None


# ----------------------------------------------------------------------------- estado
class State:
    def __init__(self, label: str, hpa_min: int, hpa_max: int, cpu: int):
        self.lock = threading.Lock()
        self.label, self.min, self.max, self.cpu_target = label, hpa_min, hpa_max, cpu
        self.t_rec = time.time()
        self.t_k6: float | None = None
        self.t_k6_end: float | None = None
        self.phase = "preparando"
        self.current = self.desired = self.cpu = None
        self.pods = self.ready = self.pending = self.terminating = 0
        self.timeline: list[dict] = []

    def snapshot(self) -> dict:
        with self.lock:
            now = time.time()
            return {
                "label": self.label, "phase": self.phase, "now": datetime.now().strftime("%H:%M:%S"),
                "rec": mmss(now - self.t_rec),
                "k6": mmss((self.t_k6_end or now) - self.t_k6) if self.t_k6 else None,
                "current": self.current, "desired": self.desired, "cpu": self.cpu,
                "cpu_target": self.cpu_target, "max": self.max,
                "pods": self.pods, "ready": self.ready, "pending": self.pending,
                "terminating": self.terminating,
            }


def poll_cluster(kube: Kube, st: State, stop: threading.Event) -> None:
    """A cada ≈ 3 s: réplicas do HPA, CPU média e estado dos pods do gateway."""
    while not stop.is_set():
        hpa = kube.json("get", "hpa", "gateway")
        pods = kube.json("get", "pods", "-l", "app=gateway")
        row = {"t": round(time.time(), 1)}
        with st.lock:
            if hpa:
                s = hpa.get("status", {})
                st.current, st.desired = s.get("currentReplicas"), s.get("desiredReplicas")
                cpu = None
                for m in s.get("currentMetrics") or []:
                    cpu = (m.get("resource") or {}).get("current", {}).get("averageUtilization")
                st.cpu = cpu
            if pods:
                items = pods.get("items", [])
                st.pods = len(items)
                st.terminating = sum(1 for p in items if p["metadata"].get("deletionTimestamp"))
                # sem espaço em nenhum nó (o "Pending" de quem só está criando o container não conta)
                st.pending = sum(1 for p in items if any(
                    c["type"] == "PodScheduled" and c["status"] == "False" for c in p["status"].get("conditions", [])))
                st.ready = sum(1 for p in items if not p["metadata"].get("deletionTimestamp") and any(
                    c["type"] == "Ready" and c["status"] == "True" for c in p["status"].get("conditions", [])))
            row.update(phase=st.phase, current=st.current, desired=st.desired, cpu=st.cpu,
                       pods=st.pods, ready=st.ready, pending=st.pending, terminating=st.terminating)
            st.timeline.append(row)
        stop.wait(3)


# ----------------------------------------------------------------------------- "terminais"
class Pane:
    def __init__(self, idx: int, title: str, logdir: Path):
        self.title = title
        self.path = logdir / f"terminal{idx + 1}.log"
        self.f = open(self.path, "wb", buffering=0)
        self.procs: list[subprocess.Popen] = []

    def run(self, argv: list[str], display: str, env: dict | None = None, wait: bool = False):
        self.f.write(f"$ {display}\n".encode())
        p = subprocess.Popen(argv, stdout=self.f, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                             env=env, cwd=ROOT)
        self.procs.append(p)
        if wait:
            p.wait()
        return p

    def stop(self) -> None:
        for p in self.procs:
            if p.poll() is None:
                p.terminate()


PAGE = r"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><title>HPA</title><style>
:root{--bg:#0c0c0c;--fg:#d4d4d4;--dim:#8a8a8a;--bar:#1c1c1c;--line:#333;--g:#3fd16b;--y:#e5c07b;--c:#56b6c2;--r:#ef596f;--b:#61afef}
*{box-sizing:border-box}html,body{margin:0;height:100%;background:#000;color:var(--fg)}
body{font:14px/1.38 "Cascadia Mono","Cascadia Code",Consolas,monospace}
header{height:52px;display:flex;align-items:center;gap:14px;padding:0 16px;background:var(--bar);
 border-bottom:1px solid var(--line);font-family:"Segoe UI",system-ui,sans-serif;font-size:16px;white-space:nowrap}
header b{font-size:18px}.tag{padding:3px 9px;border-radius:5px;background:#2b2b2b;color:#ddd}
.chip{padding:3px 10px;border-radius:5px;background:#262626}.chip em{font-style:normal;font-weight:700;color:#fff}
.hot em{color:var(--y)}.ok em{color:var(--g)}.warn em{color:var(--r)}
.sp{flex:1}.clock{color:var(--dim)}.clock em{font-style:normal;color:#fff;font-weight:600}
main{display:grid;grid-template-columns:45fr 55fr;grid-template-rows:36% 1fr;grid-template-areas:"a c" "b c";
 gap:8px;padding:8px;height:calc(100% - 52px)}
.pane{background:var(--bg);border:1px solid var(--line);border-radius:7px;display:flex;flex-direction:column;min-height:0}
.pane h2{margin:0;padding:6px 12px;font:600 14px "Segoe UI",system-ui,sans-serif;background:var(--bar);
 color:#e6e6e6;border-bottom:1px solid var(--line);border-radius:7px 7px 0 0}
.out{flex:1;min-height:0;overflow:hidden;padding:8px 12px;display:flex;flex-direction:column;justify-content:flex-end}
.out div{white-space:pre;min-height:1.38em}.out div.p{white-space:pre-wrap;color:var(--b)}
.g{color:var(--g)}.y{color:var(--y)}.c{color:var(--c)}.r{color:var(--r)}.d{color:var(--dim)}
.b{color:var(--b)}.m{color:#c678dd}.w{color:#fff}.bo{font-weight:700}
</style></head><body>
<header><b>Teste de carga com HPA</b><span class="tag" id="label"></span>
<span class="chip" id="rep"></span><span class="chip" id="cpu"></span><span class="chip" id="pend"></span>
<span class="sp"></span><span id="phase" class="d"></span><span class="clock" id="clock"></span></header>
<main>
<section class="pane" style="grid-area:a"><h2 id="t0"></h2><div class="out" id="o0"></div></section>
<section class="pane" style="grid-area:b"><h2 id="t1"></h2><div class="out" id="o1"></div></section>
<section class="pane" style="grid-area:c"><h2 id="t2"></h2><div class="out" id="o2"></div></section>
</main><script>
const TITLES = __TITLES__; const MAX = 220; const off = [0, 0, 0];
TITLES.forEach((t, i) => document.getElementById('t' + i).textContent = t);
const esc = s => s.replace(/[&<>]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;'}[c]));
// cores ANSI (o resumo do k6 vem colorido); outras sequências de controle são descartadas
const SGR = {1: 'bo', 2: 'd', 31: 'r', 32: 'g', 33: 'y', 34: 'b', 35: 'm', 36: 'c', 37: 'w',
             90: 'd', 91: 'r', 92: 'g', 93: 'y', 94: 'b', 95: 'm', 96: 'c'};
function ansi(s) {
  s = s.replace(/\x1b\[[\d;?]*[A-Za-z]/g, x => x.endsWith('m') ? x : '');
  let out = '', open = 0, last = 0, m; const re = /\x1b\[([\d;]*)m/g;
  while ((m = re.exec(s))) {
    out += esc(s.slice(last, m.index)); last = re.lastIndex;
    for (const c of (m[1] || '0').split(';').map(Number)) {
      if (c === 0) { out += '</span>'.repeat(open); open = 0; }
      else if (SGR[c]) { out += '<span class="' + SGR[c] + '">'; open++; }
    }
  }
  return out + esc(s.slice(last)) + '</span>'.repeat(open);
}
function paint(line) {
  if (line.startsWith('$ ')) return ['p', '$ ' + esc(line.slice(2))];
  let h = ansi(line);
  h = h.replace(/\b(Running)\b/g, '<span class="g">$1</span>')
       .replace(/\b(Pending)\b/g, '<span class="y">$1</span>')
       .replace(/\b(ContainerCreating|PodInitializing)\b/g, '<span class="c">$1</span>')
       .replace(/\b(Terminating|Completed|Error|CrashLoopBackOff|OOMKilled|Evicted)\b/g, '<span class="r">$1</span>')
       .replace(/(cpu: )(\d+)(%\/\d+%)/g, (m, a, n, b) => a + '<span class="' + (+n > __TARGET__ ? 'y' : 'g') + '">' + n + '</span>' + b)
       .replace(/(✓)/g, '<span class="g">$1</span>').replace(/(✗)/g, '<span class="r">$1</span>');
  return ['', h];
}
async function pull(i) {
  const j = await (await fetch('/log/' + i + '?from=' + off[i])).json();
  off[i] = j.next; if (!j.text) return;
  const box = document.getElementById('o' + i);
  for (const line of j.text.replace(/\r/g, '').split('\n')) {
    const [cls, html] = paint(line); const d = document.createElement('div');
    if (cls) d.className = cls; d.innerHTML = html; box.appendChild(d);
  }
  while (box.children.length > MAX) box.removeChild(box.firstChild);
}
function chip(id, cls, html) { const e = document.getElementById(id); e.className = 'chip ' + cls; e.innerHTML = html; }
async function tick() {
  try {
    await Promise.all([0, 1, 2].map(pull));
    const s = await (await fetch('/status')).json();
    document.getElementById('label').textContent = s.label;
    chip('rep', s.current > 2 ? 'hot' : '', 'réplicas do gateway <em>' + (s.current ?? '–') + '</em>' +
         (s.desired != null && s.desired !== s.current ? ' → <em>' + s.desired + '</em>' : '') + ' / máx. ' + s.max);
    chip('cpu', s.cpu > s.cpu_target ? 'hot' : 'ok', 'CPU média <em>' + (s.cpu ?? '–') + '%</em> / alvo ' + s.cpu_target + '%');
    chip('pend', s.pending ? 'warn' : '', 'sem espaço nos nós <em>' + s.pending + '</em>');
    document.getElementById('phase').textContent = s.phase;
    document.getElementById('clock').innerHTML = 'gravação <em>' + s.rec + '</em>' +
      (s.k6 ? ' · k6 <em>' + s.k6 + '</em>' : '') + ' · ' + s.now;
  } catch (e) {}
}
setInterval(tick, 400); tick();
</script></body></html>"""


def serve(panes: list[Pane], st: State, titles: list[str]) -> http.server.ThreadingHTTPServer:
    page = PAGE.replace("__TITLES__", json.dumps(titles)).replace("__TARGET__", str(st.cpu_target)).encode()

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, body: bytes, ctype: str):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urllib.parse.urlparse(self.path)
            if u.path == "/":
                return self.send(page, "text/html; charset=utf-8")
            if u.path == "/status":
                return self.send(json.dumps(st.snapshot()).encode(), "application/json")
            if u.path.startswith("/log/"):
                i = int(u.path.rsplit("/", 1)[1])
                start = int(urllib.parse.parse_qs(u.query).get("from", ["0"])[0])
                with open(panes[i].path, "rb") as f:
                    f.seek(start)
                    data = f.read()
                cut = data.rfind(b"\n") + 1          # só linhas completas
                text = data[:cut].decode("utf-8", "replace").rstrip("\n")
                return self.send(json.dumps({"text": text, "next": start + cut}).encode(), "application/json")
            self.send_error(404)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def record(url: str, frames_dir: Path, fps: float, stop: threading.Event, out: list, browser: str) -> None:
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        b = p.chromium.launch(channel=browser, headless=True)
        page = b.new_page(viewport={"width": 1920, "height": 1080})
        page.goto(url)
        page.wait_for_timeout(1500)
        n, step = 0, 1 / fps
        while not stop.is_set():
            t = time.time()
            path = frames_dir / f"{n:06d}.png"
            page.screenshot(path=str(path))
            out.append((path, t))
            n += 1
            stop.wait(max(0.0, step - (time.time() - t)))
        b.close()


def encode(frames: list, out_mp4: Path, speed: float = 1.0) -> None:
    import imageio_ffmpeg
    lst = out_mp4.with_suffix(".txt")
    with open(lst, "w", encoding="utf-8") as f:
        f.write("ffconcat version 1.0\n")
        for (path, t), nxt in zip(frames, frames[1:] + [None]):
            f.write(f"file '{path.as_posix()}'\n")
            f.write(f"duration {((nxt[1] - t) if nxt else 1.0):.3f}\n")
        f.write(f"file '{frames[-1][0].as_posix()}'\n")
    vf = (f"setpts=PTS/{speed}," if speed != 1 else "") + "fps=10,format=yuv420p"
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                    "-i", str(lst), "-vf", vf, "-c:v", "libx264", "-preset", "medium", "-tune", "stillimage",
                    "-crf", "24", "-movflags", "+faststart", str(out_mp4)], check=True)
    lst.unlink()


# ----------------------------------------------------------------------------- Grafana
PANELS = {  # id do painel em setup/grafana/dashboards/store.json -> nome do arquivo
    16: "replicas-no-tempo", 3: "replicas", 1: "req-s", 7: "req-s-por-replica", 10: "cpu-por-replica",
    6: "latencia-gateway", 14: "k6-vus-req-s", 15: "k6-latencia",
}


def grafana_prints(port: int, password: str, t_from: float, t_to: float, out: Path, browser: str,
                   prefix: str = "grafana") -> list[Path]:
    from playwright.sync_api import sync_playwright
    from PIL import Image, ImageChops
    auth = base64.b64encode(f"admin:{password}".encode()).decode()
    rng = f"orgId=1&from={int(t_from * 1000)}&to={int(t_to * 1000)}&timezone=browser"
    base = f"http://127.0.0.1:{port}"
    files = []
    with sync_playwright() as p:
        b = p.chromium.launch(channel=browser, headless=True)
        # alto o bastante para o dashboard inteiro (o Grafana só desenha os painéis visíveis)
        ctx = b.new_context(viewport={"width": 1920, "height": 2500}, extra_http_headers={"Authorization": f"Basic {auth}"})
        page = ctx.new_page()
        page.goto(f"{base}/d/store-platform/store?{rng}&kiosk", wait_until="domcontentloaded")
        page.wait_for_timeout(9000)
        full = out / f"{prefix}-dashboard.png"
        page.screenshot(path=str(full))
        # corta o fundo vazio embaixo do último painel
        im = Image.open(full).convert("RGB")
        bg = Image.new("RGB", im.size, im.getpixel((5, im.height - 5)))
        box = ImageChops.difference(im, bg).getbbox()
        if box:
            im.crop((0, 0, im.width, min(im.height, box[3] + 16))).save(full)
        files.append(full)
        page.set_viewport_size({"width": 1100, "height": 440})
        for pid, name in PANELS.items():
            page.goto(f"{base}/d-solo/store-platform/store?{rng}&panelId={pid}", wait_until="domcontentloaded")
            page.wait_for_timeout(4000)
            f = out / f"{prefix}-{name}.png"
            page.screenshot(path=str(f))
            files.append(f)
        b.close()
    return files


def all_prints(kube: Kube, port: int, t: dict, out: Path, browser: str) -> None:
    """Dois conjuntos: até o fim do k6 (o "durante o teste", réplicas no máximo) e a gravação inteira."""
    pw = base64.b64decode(kube.run("get", "secret", "grafana-admin", "-o", "jsonpath={.data.password}").stdout).decode()
    start = t.get("prints_de", t["gravacao"])
    grafana_prints(port, pw, start, t["k6_fim"] + 15, out, browser, "grafana-pico")
    grafana_prints(port, pw, start, t["fim"], out, browser, "grafana")


def wait_prometheus_replicas(port: int, n: int, timeout: float = 90) -> None:
    """O Prometheus leva ≈ 15-30 s para notar réplicas removidas: sem esperar, o começo dos gráficos
    mostraria as réplicas que sobraram de uma rodada anterior."""
    q = urllib.parse.urlencode({"query": 'sum(up{job="gateway"})'})
    end = time.time() + timeout
    while time.time() < end:
        try:
            res = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/v1/query?{q}", timeout=5))
            v = res["data"]["result"]
            if v and round(float(v[0]["value"][1])) == n:
                return
        except Exception:
            pass
        time.sleep(2)
    print(f"   (o Prometheus ainda não vê {n} réplicas; seguindo assim mesmo)")


def port_forward(kube: Kube, svc: str, port: int, target: int, log) -> subprocess.Popen:
    p = subprocess.Popen(kube.argv("port-forward", f"svc/{svc}", f"{port}:{target}"), stdout=log, stderr=subprocess.STDOUT)
    wait_port(port)
    return p


# ----------------------------------------------------------------------------- principal
def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--context", help="contexto do kubectl (padrão: o atual)")
    ap.add_argument("--namespace", default="store")
    ap.add_argument("--base-url", default="http://host.docker.internal:8081", help="endereço do lb/ELB visto pelo k6")
    ap.add_argument("--name", default="kind-hpa", help="nome do resultado (load/results/<name>...)")
    ap.add_argument("--label", default="ensaio local (kind, 2 nós)")
    ap.add_argument("--min", type=int, default=2)
    ap.add_argument("--max", type=int, default=6)
    ap.add_argument("--cpu", type=int, default=50, help="alvo de CPU do HPA, em %% do request")
    ap.add_argument("--vus", default="100")
    ap.add_argument("--duration", default="4m")
    ap.add_argument("--scenario", default="auth")
    ap.add_argument("--cooldown", type=int, default=420, help="s máximos esperando o HPA voltar ao mínimo")
    ap.add_argument("--fps", type=float, default=2)
    ap.add_argument("--fast", type=float, default=8, help="velocidade do vídeo acelerado")
    ap.add_argument("--prom-port", type=int, default=9090)
    ap.add_argument("--grafana-port", type=int, default=3000)
    ap.add_argument("--browser", default="msedge", help="msedge | chrome | chromium")
    ap.add_argument("--keep-hpa", action="store_true", help="não apagar o HPA no fim")
    ap.add_argument("--keep-frames", action="store_true")
    ap.add_argument("--prints-only", action="store_true",
                    help="só refaz os prints do Grafana de uma gravação já feita (usa o resumo.json)")
    a = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # console do Windows

    kube = Kube(a.context, a.namespace)
    out = ROOT / "load" / "results" / a.name
    if a.prints_only:
        t = json.loads((out / "resumo.json").read_text(encoding="utf-8"))["t"]
        with open(out / "port-forward.log", "ab") as log:
            pf = port_forward(kube, "grafana", a.grafana_port, 3000, log)
            try:
                all_prints(kube, a.grafana_port, t, out, a.browser)
            finally:
                pf.terminate()
        print(">> prints em", out)
        return
    frames_dir = out / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir(parents=True)

    # 0. pré-requisitos e estado inicial: sem HPA, gateway com 2 réplicas
    if not kube.json("get", "deployment", "gateway"):
        sys.exit("deployment gateway não encontrado (contexto/namespace certos?)")
    print(">> zerando: sem HPA, gateway com", a.min, "réplicas")
    kube.run("delete", "hpa", "gateway", "--ignore-not-found")
    kube.run("scale", "deployment", "gateway", f"--replicas={a.min}")
    kube.run("rollout", "status", "deployment/gateway", "--timeout=300s")
    for _ in range(120):
        pods = kube.json("get", "pods", "-l", "app=gateway") or {"items": []}
        if len(pods["items"]) == a.min:
            break
        time.sleep(2)
    for _ in range(60):
        if kube.run("top", "pods", "-l", "app=gateway", check=False).returncode == 0:
            break
        print("   esperando o metrics-server...")
        time.sleep(5)

    # port-forwards: Prometheus (métricas do k6) e Grafana (prints)
    pf_log = open(out / "port-forward.log", "wb")
    pfs = [port_forward(kube, "prometheus", a.prom_port, 9090, pf_log),
           port_forward(kube, "grafana", a.grafana_port, 3000, pf_log)]
    wait_prometheus_replicas(a.prom_port, a.min)

    st = State(a.label, a.min, a.max, a.cpu)
    titles = ["1 · HPA do gateway", "2 · Pods do gateway", "3 · k6 (cenário " + a.scenario + ", " + a.vus + " VUs)"]
    panes = [Pane(i, t, out) for i, t in enumerate(titles)]
    srv = serve(panes, st, titles)
    url = f"http://127.0.0.1:{srv.server_address[1]}/"

    stop_poll, stop_rec = threading.Event(), threading.Event()
    frames: list = []
    threading.Thread(target=poll_cluster, args=(kube, st, stop_poll), daemon=True).start()
    rec = threading.Thread(target=record, args=(url, frames_dir, a.fps, stop_rec, frames, a.browser), daemon=True)
    rec.start()
    time.sleep(3)
    print(">> gravando:", url)

    # 1 e 2: HPA e pods
    hpa_args = ("autoscale", "deployment", "gateway", f"--cpu={a.cpu}%", f"--min={a.min}", f"--max={a.max}")
    panes[0].run(kube.argv(*hpa_args), kube.display(*hpa_args), wait=True)
    panes[0].run(kube.argv("get", "hpa", "gateway", "-w"), kube.display("get", "hpa", "gateway", "-w"))
    pod_args = ("get", "pods", "-l", "app=gateway", "-o", "wide", "-w")
    panes[1].run(kube.argv(*pod_args), kube.display(*pod_args))
    for _ in range(40):                       # o HPA lê a primeira métrica (sai do <unknown>)
        if st.cpu is not None:
            break
        time.sleep(1.5)
    time.sleep(5)

    # 3: k6
    prom = f"http://host.docker.internal:{a.prom_port}"
    env_k6 = {"BASE_URL": a.base_url, "PROMETHEUS_RW_URL": prom, "NAME": a.name, "SCENARIO": a.scenario,
              "PROFILE": "load", "VUS": a.vus, "DURATION": a.duration, "THINK": "0"}
    shown = " ".join(f"{k}={v}" for k, v in env_k6.items()) + " ./scripts/load-test.sh"
    with st.lock:
        st.phase, st.t_k6 = "k6 rodando", time.time()
    print(">> k6:", shown)
    k6 = panes[2].run([find_bash(), "scripts/load-test.sh"], shown, env={**os.environ, **env_k6})
    k6.wait()
    with st.lock:
        st.t_k6_end = time.time()
        st.phase = "k6 terminou · o HPA espera 5 min sem carga para reduzir"
    print(f">> k6 terminou (código {k6.returncode}); esperando o HPA voltar a {a.min}")

    # depois do k6: grava a volta para o mínimo
    end = time.time() + a.cooldown
    stable_since = None
    while time.time() < end:
        with st.lock:
            back = st.current == a.min and st.pods == a.min and st.terminating == 0
        stable_since = (stable_since or time.time()) if back else None
        if stable_since and time.time() - stable_since > 12:
            break
        time.sleep(2)
    with st.lock:
        st.phase = "fim: de volta a " + str(st.current) + " réplicas"
    time.sleep(4)
    t_end = time.time()

    stop_rec.set()
    rec.join()
    stop_poll.set()
    for p in panes:
        p.stop()
    srv.shutdown()

    # vídeos
    print(f">> {len(frames)} quadros; gerando vídeos")
    encode(frames, out / "hpa-tempo-real.mp4")
    encode(frames, out / f"hpa-{int(a.fast)}x.mp4", a.fast)

    # linha do tempo (t relativo ao início do k6) e resumo
    t0 = st.t_k6
    with open(out / "linha-do-tempo.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["t_k6_s", "phase", "current", "desired", "cpu", "pods", "ready",
                                          "pending", "terminating"])
        w.writeheader()
        for r in st.timeline:
            w.writerow({"t_k6_s": round(r["t"] - t0, 1), **{k: r[k] for k in w.fieldnames if k in r}})
    tl = [r for r in st.timeline if r["t"] >= t0]
    first_up = next((r["t"] - t0 for r in tl if (r["desired"] or 0) > a.min), None)
    max_cur = max((r["current"] or 0) for r in tl) if tl else None
    t_max = next((r["t"] - t0 for r in tl if r["current"] == max_cur), None)
    max_ready = max((r["ready"] or 0) for r in tl) if tl else None
    t_ready = next((r["t"] - t0 for r in tl if r["ready"] == max_ready), None)
    after = [r for r in tl if r["t"] >= st.t_k6_end]
    back = next((r["t"] - st.t_k6_end for r in after if r["current"] == a.min and r["pods"] == a.min), None)
    row = ROOT / "load" / "results" / f"{a.name}.row.md"
    resumo = {
        "label": a.label, "inicio": datetime.fromtimestamp(t0).isoformat(timespec="seconds"),
        "hpa": {"min": a.min, "max": a.max, "cpu_alvo_pct": a.cpu},
        "k6": {"vus": a.vus, "duracao": a.duration, "cenario": a.scenario, "codigo_saida": k6.returncode,
               "linha": row.read_text(encoding="utf-8").strip() if row.exists() else None},
        "primeiro_aumento_s": round(first_up, 1) if first_up is not None else None,
        "replicas_max": max_cur, "replicas_max_em_s": round(t_max, 1) if t_max is not None else None,
        "prontas_max": max_ready, "prontas_max_em_s": round(t_ready, 1) if t_ready is not None else None,
        "cpu_max_pct": max((r["cpu"] or 0) for r in tl) if tl else None,
        "pending_max": max((r["pending"] or 0) for r in tl) if tl else None,
        "volta_ao_minimo_s_apos_k6": round(back, 1) if back is not None else None,
        "duracao_gravacao_s": round(t_end - st.t_rec, 1),
        "t": {"gravacao": st.t_rec, "k6": st.t_k6, "k6_fim": st.t_k6_end, "fim": t_end},
    }
    (out / "resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")

    print(">> prints do Grafana")
    try:
        all_prints(kube, a.grafana_port, resumo["t"], out, a.browser)
    except Exception as e:  # o vídeo já está salvo: refazer com --prints-only
        print("   falhou:", e, "(refazer com --prints-only)")

    for p in pfs:
        p.terminate()
    if not a.keep_hpa:
        kube.run("delete", "hpa", "gateway", "--ignore-not-found", check=False)
    if not a.keep_frames:
        shutil.rmtree(frames_dir, ignore_errors=True)
    print(json.dumps(resumo, ensure_ascii=False, indent=2))
    print(">> arquivos em", out)


if __name__ == "__main__":
    main()
