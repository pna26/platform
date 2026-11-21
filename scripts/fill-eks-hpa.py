#!/usr/bin/env python3
"""Preenche a seção "Resultado no EKS" de docs/devops/load-testing.md com a rodada eks-hpa.

Lê o que o scripts/hpa-record.py gravou em load/results/<name>/ (resumo.json, com a linha do k6
e a linha do tempo do HPA) e:
  - troca a linha "| eks-hpa | ..." da tabela pelos números medidos;
  - escreve, entre os marcadores <!-- eks-hpa:inicio --> e <!-- eks-hpa:fim -->, a linha do tempo,
    o vídeo acelerado e os prints do Grafana;
  - copia o vídeo acelerado e os prints para docs/devops/hpa/ com o prefixo eks-hpa-.

Só usa números do resumo: o que não foi medido fica "—". Rodar de novo sobrescreve.

    python3 scripts/fill-eks-hpa.py                    # load/results/eks-hpa -> docs/devops/load-testing.md
    python3 scripts/fill-eks-hpa.py --name eks-hpa --doc docs/devops/load-testing.md --no-copy
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
START, END = "<!-- eks-hpa:inicio -->", "<!-- eks-hpa:fim -->"


def fmt_s(v) -> str:
    if v is None:
        return "—"
    v = float(v)
    return f"+{int(v // 60)} min {int(v % 60)} s" if v >= 60 else f"+{v:.0f} s"


def num(v: str) -> str:
    """'435094' -> '435.094' ; '1606.5' -> '1.606,5' (como nas outras tabelas da página)."""
    v = v.strip()
    if not re.fullmatch(r"-?\d+(\.\d+)?", v):
        return v
    i, _, d = v.partition(".")
    i = f"{int(i):,}".replace(",", ".")
    return f"{i},{d}" if d else i


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="eks-hpa")
    ap.add_argument("--doc", default="docs/devops/load-testing.md")
    ap.add_argument("--results", default=None, help="padrão: load/results/<name>")
    ap.add_argument("--no-copy", action="store_true", help="não copia vídeo/prints para docs/devops/hpa/")
    a = ap.parse_args()

    out = Path(a.results) if a.results else ROOT / "load" / "results" / a.name
    doc = Path(a.doc) if Path(a.doc).is_absolute() else ROOT / a.doc
    r = json.loads((out / "resumo.json").read_text(encoding="utf-8"))

    # linha do k6: | NAME | SCENARIO | PROFILE | VUs | reqs | req/s | p50 | p95 | p99 | erros |
    k6 = (r.get("k6") or {}).get("linha")
    if not k6:
        sys.exit(f"{out}/resumo.json sem a linha do k6: o k6 terminou? (código {r.get('k6', {}).get('codigo_saida')})")
    c = [x.strip() for x in k6.strip().strip("|").split("|")]
    if len(c) < 10:
        sys.exit(f"linha do k6 inesperada: {k6}")
    _, _, _, vus, reqs, rate, p50, p95, p99, err = c[:10]
    hpa = r.get("hpa", {})
    rep = f"{hpa.get('min', '—')} → {r.get('replicas_max') if r.get('replicas_max') is not None else '—'}"
    row = f"| {a.name} | {vus} | {num(reqs)} | {num(rate)} | {p50} | {p95} | {p99} | {err.replace('.', ',')} | {rep} |"

    text = doc.read_text(encoding="utf-8")
    text, n = re.subn(rf"^\| {re.escape(a.name)} \|.*\|$", row, text, count=1, flags=re.M)
    if n != 1:
        sys.exit(f"não achei a linha '| {a.name} | ...' na tabela de {doc}")

    # vídeo e prints
    media = []
    if not a.no_copy:
        dst = doc.parent / "hpa"
        dst.mkdir(exist_ok=True)
        fast = sorted(out.glob("hpa-*x.mp4"))
        if fast:
            shutil.copy2(fast[0], dst / f"{a.name}-{fast[0].name.split('-', 1)[1]}")
            media.append(("video", f"{a.name}-{fast[0].name.split('-', 1)[1]}"))
        for png in sorted(out.glob("grafana-*.png")):
            name = "grafana" if png.stem == "grafana-dashboard" else png.stem.removeprefix("grafana-")
            shutil.copy2(png, dst / f"{a.name}-{name}.png")
            media.append(("png", f"{a.name}-{name}.png"))

    cpu = r.get("cpu_max_pct")
    lines = [
        START,
        "",
        f"Rodada de {r.get('inicio', '—').replace('T', ' ')} ({r.get('label', '')}): cenário "
        f"`{(r.get('k6') or {}).get('cenario', '—')}`, {(r.get('k6') or {}).get('vus', '—')} VUs por "
        f"{(r.get('k6') or {}).get('duracao', '—')}, HPA com alvo de {hpa.get('cpu_alvo_pct', '—')} % de CPU "
        f"(mín. {hpa.get('min', '—')}, máx. {hpa.get('max', '—')}).",
        "",
        "| Tempo desde o início do k6 | O que aconteceu |",
        "|---|---|",
        f"| {fmt_s(r.get('primeiro_aumento_s'))} | o HPA pede mais réplicas pela primeira vez |",
        f"| {fmt_s(r.get('replicas_max_em_s'))} | **{r.get('replicas_max', '—')}** réplicas pedidas (máximo da rodada) |",
        f"| {fmt_s(r.get('prontas_max_em_s'))} | **{r.get('prontas_max', '—')}** réplicas prontas |",
        f"| fim do k6 + {fmt_s(r.get('volta_ao_minimo_s_apos_k6')).lstrip('+')} | o HPA volta para {hpa.get('min', '—')} réplicas |"
        if r.get("volta_ao_minimo_s_apos_k6") is not None else
        "| — | a volta ao mínimo não foi registrada (gravação terminou antes) |",
        "",
        f"CPU média máxima do gateway: **{cpu} %** do `request`; pods *Pending* no pico: **{r.get('pending_max', '—')}**.",
        "",
    ]
    for kind, f in media:
        if kind == "video":
            lines += [f'<video controls preload="metadata" width="100%" src="../hpa/{f}"></video>', ""]
    for kind, f in media:
        if kind == "png":
            lines += [f"![Teste com HPA no EKS: {f.removeprefix(a.name + '-').replace('-', ' ')}](hpa/{f})", ""]
    lines.append(END)
    block = "\n".join(lines)

    if START in text and END in text:
        text = re.sub(re.escape(START) + r".*?" + re.escape(END), lambda _: block, text, count=1, flags=re.S)
    else:
        sys.exit(f"marcadores {START} / {END} não encontrados em {doc}")
    doc.write_text(text, encoding="utf-8")
    print(row)
    print(f">> {doc.relative_to(ROOT) if doc.is_relative_to(ROOT) else doc} atualizado; mídia: {[f for _, f in media]}")


if __name__ == "__main__":
    main()
