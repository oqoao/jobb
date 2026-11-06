#!/usr/bin/env python3
"""Lokal jobbsida: letar själv upp nya annonser, låter AI:n betygsätta dem och visar
dem från bäst till sämst. Du väljer vilka du vill ha brev till – brevet dyker upp direkt.

    python sida.py            # öppnar http://localhost:8000 i webbläsaren
    python sida.py --port 8080 --utan-webblasare

Sökningen och betygen fungerar som i daglig.py (län, yrkesområden och önskemål i
private/installningar.toml). Annonser som redan betygsatts betygsätts inte igen.
Breven sparas i private/brev/.
"""
import argparse
import datetime
import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import requests

import gemensamt as g
from daglig import bedom_nya, las_installningar, las_sedda, spara_sedda

SIDA = g.ROOT / "sida.html"
sedda_las = threading.Lock()      # skyddar läs-ändra-spara av sedda.json
uppdatering_las = threading.Lock()  # bara en sökning åt gången


def lista():
    idag = datetime.date.today().isoformat()
    annonser = []
    for i, s in las_sedda().items():
        if s["status"] in ("dold", "borttagen") or (s.get("sista_dag") and s["sista_dag"] < idag):
            continue
        annonser.append({
            "id": i, "rubrik": s["rubrik"], "arbetsgivare": s["arbetsgivare"], "ort": s["ort"],
            "sista_dag": s.get("sista_dag", ""), "lank": f"https://arbetsformedlingen.se/platsbanken/annonser/{i}",
            "poang": s.get("poang", 0), "status": s["status"], "ny": s.get("datum") == idag,
            "varfor": s.get("skal") if s["status"] == "bortsorterad" else s.get("varfor", ""),
            "saknas": s.get("saknas", []), "har_brev": bool(s.get("brev")),
        })
    annonser.sort(key=lambda a: (-a["poang"], a["sista_dag"] or "9999"))
    return {"annonser": annonser}


def uppdatera():
    if not uppdatering_las.acquire(blocking=False):
        return {"nya": 0, "fel": [], "pagar": True}
    try:
        _, poster, fel = bedom_nya(las_installningar(), las_sedda())
        with sedda_las:
            sedda = las_sedda()
            sedda.update({i: p for i, p in poster.items() if i not in sedda})
            spara_sedda(sedda)
        return {"nya": len(poster), "fel": fel}
    finally:
        uppdatering_las.release()


def annonstext(annons_id):
    try:
        a = g.hamta_annons(annons_id)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return {"text": "Annonsen finns inte längre på Platsbanken – den har troligen tagits bort."}
        raise
    return {"text": (a.get("description") or {}).get("text") or ""}


def las_brevfil(relativ):
    """Plockar ut själva brevet och granskningens ändringar ur en sparad brevfil."""
    delar = (g.PRIVAT / relativ).read_text(encoding="utf-8").split("\n---\n")
    brev = delar[1].strip() if len(delar) > 1 else delar[0]
    andringar = []
    if len(delar) > 2 and "## Granskningen ändrade" in delar[2]:
        block = delar[2].split("## Granskningen ändrade", 1)[1].split("\n## ", 1)[0]
        andringar = [rad for rad in block.splitlines() if rad.startswith("- ")]
    return brev, andringar


def befintligt_brev(annons_id):
    s = las_sedda().get(annons_id) or {}
    if s.get("brev") and (g.PRIVAT / s["brev"]).exists():
        brev, andringar = las_brevfil(s["brev"])
        return {"brev": brev, "andringar": andringar, "fil": f"private/{s['brev']}"}
    return None


def skriv(annons_id):
    if (finns := befintligt_brev(annons_id)):
        return finns
    a = g.hamta_annons(annons_id)
    fil, brev, andringar = g.skriv_och_granska(
        (g.ROOT / "prompt_brev.md").read_text(encoding="utf-8"),
        (g.ROOT / "prompt_granskning.md").read_text(encoding="utf-8"),
        g.PROFIL.read_text(encoding="utf-8"), a, las_installningar().get("modell_brev", "deepseek-reasoner"))
    idag = datetime.date.today().isoformat()
    relativ = fil.relative_to(g.PRIVAT).as_posix()
    with sedda_las:
        sedda = las_sedda()
        post = sedda.get(annons_id) or {
            "datum": idag, "rubrik": a.get("headline"), "arbetsgivare": g.arbetsgivare(a), "ort": g.ort(a),
            "sista_dag": (a.get("application_deadline") or "")[:10], "sortera_bort": False, "skal": "",
            "poang": 0, "varfor": "", "saknas": []}
        post.update(status="brev", brev=relativ, brev_datum=idag)
        sedda[annons_id] = post
        spara_sedda(sedda)
    return {"brev": brev, "andringar": andringar or [], "fil": f"private/{relativ}"}


def dolj(annons_id):
    with sedda_las:
        sedda = las_sedda()
        if annons_id in sedda:
            sedda[annons_id]["status"] = "dold"
            spara_sedda(sedda)
    return {"ok": True}


class Hanterare(BaseHTTPRequestHandler):
    def do_GET(self):
        vag = urlparse(self.path).path
        if vag == "/":
            return self.svara(200, SIDA.read_bytes(), "text/html; charset=utf-8")
        if vag == "/api/lista":
            return self.kor(lista)
        if vag.startswith("/api/annons/") and (i := vag.rsplit("/", 1)[1]).isdigit():
            return self.kor(lambda: annonstext(i))
        if vag.startswith("/api/brev/") and (i := vag.rsplit("/", 1)[1]).isdigit():
            return self.kor(lambda: befintligt_brev(i) or {"brev": None})
        self.svara(404, b"Hittades inte", "text/plain; charset=utf-8")

    def do_POST(self):
        # Kräver en egen header så att andra webbsidor inte kan få din dator att dra tokens.
        if self.headers.get("X-Jobb") != "1":
            return self.svara(403, b"Nekad", "text/plain; charset=utf-8")
        vag = urlparse(self.path).path
        if vag == "/api/uppdatera":
            return self.kor(uppdatera)
        if vag.startswith("/api/brev/") and (i := vag.rsplit("/", 1)[1]).isdigit():
            return self.kor(lambda: skriv(i))
        if vag.startswith("/api/dolj/") and (i := vag.rsplit("/", 1)[1]).isdigit():
            return self.kor(lambda: dolj(i))
        self.svara(404, b"Hittades inte", "text/plain; charset=utf-8")

    def kor(self, funktion):
        try:
            data, kod = funktion(), 200
        except g.DeepSeekFel as e:
            data, kod = {"fel": str(e)}, 502
        except requests.RequestException as e:
            data, kod = {"fel": f"Kunde inte nå Platsbanken: {e}"}, 502
        self.svara(kod, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def svara(self, kod, kropp, typ):
        self.send_response(kod)
        self.send_header("Content-Type", typ)
        self.send_header("Content-Length", str(len(kropp)))
        self.end_headers()
        self.wfile.write(kropp)

    def log_message(self, *args):
        pass


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--utan-webblasare", action="store_true", help="öppna inte webbläsaren automatiskt")
    args = p.parse_args()
    if not g.PROFIL.exists():
        raise SystemExit(f"Hittar inte {g.PROFIL} – skapa den med din CV-text och dina projekt.")
    las_installningar()  # skapar private/installningar.toml första gången
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Hanterare)
    adress = f"http://localhost:{args.port}"
    print(f"Jobbsidan körs på {adress} – stäng det här fönstret (eller tryck Ctrl+C) för att avsluta.")
    if not args.utan_webblasare:
        webbrowser.open(adress)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
