#!/usr/bin/env python3
"""Daglig körning: hämtar nya annonser, sållar bort de som inte passar, poängsätter
resten och skriver brev till de bästa. Resultatet hamnar i private/listor/<datum>.md.

    export DEEPSEEK_API_KEY=sk-...
    python daglig.py              # sålla + skriva brev
    python daglig.py --utan-brev  # bara sålla och poängsätta

Annonser som redan bedömts sparas i private/sedda.json och tas inte upp igen.
Inställningar (län, yrkesområden, körkort, poänggräns) finns i private/installningar.toml.
"""
import argparse
import datetime
import json
import re
import shutil
import sys
import tomllib
from concurrent.futures import ThreadPoolExecutor

import requests

from gemensamt import (PRIVAT, PROFIL, ROOT, DeepSeekFel, annons_som_text, arbetsgivare, fraga_deepseek,
                       hamta_annons, korkort_som_krävs, lank, ort, skriv_och_granska, sok_annonser)

INSTALLNINGAR = PRIVAT / "installningar.toml"
SEDDA = PRIVAT / "sedda.json"
LISTOR = PRIVAT / "listor"


def las_installningar():
    if not INSTALLNINGAR.exists():
        PRIVAT.mkdir(exist_ok=True)
        shutil.copy(ROOT / "installningar.exempel.toml", INSTALLNINGAR)
        print(f"Skapade {INSTALLNINGAR.relative_to(ROOT)} – ändra där om du vill söka annorlunda.")
    return tomllib.loads(INSTALLNINGAR.read_text(encoding="utf-8"))


def las_sedda():
    return json.loads(SEDDA.read_text(encoding="utf-8")) if SEDDA.exists() else {}


def spara_sedda(sedda):
    tmp = SEDDA.with_suffix(".tmp")
    tmp.write_text(json.dumps(sedda, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(SEDDA)


def hamta_nya(inst, sedda, dagar=None):
    if dagar is None:
        dagar = 3 if sedda else inst.get("forsta_korningen_dagar", 60)
    params = {"published-after": dagar * 24 * 60}
    if inst.get("lan"):
        params["region"] = inst["lan"]
    if inst.get("yrkesomraden"):
        params["occupation-field"] = inst["yrkesomraden"]
    unika = {a["id"]: a for a in sok_annonser(params)}
    return [a for i, a in unika.items() if i not in sedda]


def bedom(a, prompt, profil, inst):
    saknade = [k for k in korkort_som_krävs(a) if k not in inst.get("korkort", [])]
    if saknade:
        return {"sortera_bort": True, "skal": f"Kräver körkort {', '.join(saknade)}",
                "poang": 0, "varfor": "", "saknas": saknade}
    meddelande = (f"# PROFIL\n{profil}\n\n# ÖNSKEMÅL\n{inst.get('onskemal', '')}\n\n"
                  f"# ANNONS\n{annons_som_text(a)}")
    b = tolka(fraga_deepseek(prompt, meddelande, inst.get("modell_matchning", "deepseek-chat"),
                             json_svar=True, temperatur=0))
    # Den snabba modellen sorterar ibland bort annonser som faktiskt passar. Om den sorterat
    # bort något som ändå fått hyfsad poäng får den noggranna modellen bedöma det en gång till.
    if b["sortera_bort"] and b["poang"] >= inst.get("dubbelkolla_fran_poang", 5):
        b = tolka(fraga_deepseek(prompt, meddelande, inst.get("modell_brev", "deepseek-reasoner")))
        b["dubbelkollad"] = True
    return b


def tolka(svar):
    """Plockar ut JSON-svaret från matchningen och städar fälten."""
    m = re.search(r"\{.*\}", svar, re.S)
    if not m:
        raise ValueError(f"inget JSON i svaret: {svar[:200]}")
    b = json.loads(m.group())
    siffror = re.search(r"\d+", str(b.get("poang", "")))
    return {"sortera_bort": bool(b.get("sortera_bort")),
            "skal": str(b.get("skal") or ""),
            "poang": min(10, int(siffror.group())) if siffror else 0,
            "varfor": str(b.get("varfor") or ""),
            "saknas": [str(s) for s in (b.get("saknas") or [])]}


def rad(s, i, med_skal=False):
    text = f"{s['rubrik']} – {s['arbetsgivare']} ({s['ort']})"
    if s.get("poang"):
        text = f"{s['poang']}/10 · {text}"
    if med_skal:
        text += f": {s.get('skal') or s.get('varfor')}"
    return f"- {text} · [Annonsen](https://arbetsformedlingen.se/platsbanken/annonser/{i})"


def skriv_rapport(idag, sedda, fel):
    """Skriver (eller skriver över) dagens lista med allt som bedömts och skrivits i dag."""
    LISTOR.mkdir(parents=True, exist_ok=True)
    fil = LISTOR / f"{idag}.md"

    def lista(status, bara_idag=True):
        ut = [(i, s) for i, s in sedda.items() if s["status"] == status
              and (not bara_idag or s["datum"] == idag)
              and (not s["sista_dag"] or s["sista_dag"] >= idag)]
        return sorted(ut, key=lambda x: (-x[1].get("poang", 0), x[1]["sista_dag"] or "9999"))

    brev = [(i, s) for i, s in sedda.items() if s.get("brev_datum") == idag]
    vantar, lag, bort = lista("vantar", bara_idag=False), lista("lag_poang"), lista("bortsorterad")
    bedomda = sum(1 for s in sedda.values() if s["datum"] == idag)
    t = [f"# Jobb – {idag}\n",
         f"{bedomda} nya annonser bedömda · {len(brev)} brev skrivna · {len(vantar)} väntar på brev · "
         f"{len(lag)} låg poäng · {len(bort)} sorterades bort\n"]
    if brev:
        t.append("## Brev klara att läsa\n")
        for i, s in sorted(brev, key=lambda x: (-x[1]["poang"], x[1]["sista_dag"] or "9999")):
            t.append(f"### {s['poang']}/10 · {s['rubrik']} – {s['arbetsgivare']} ({s['ort']})\n")
            t.append(f"Sista ansökningsdag: {s['sista_dag'] or '–'} · [Annonsen](https://arbetsformedlingen.se/platsbanken/annonser/{i})"
                     f" · [Brevet](../{s['brev']})  ")
            t.append(f"Varför: {s['varfor']}  ")
            if s["saknas"]:
                t.append(f"Saknas: {', '.join(s['saknas'])}")
            t.append("")
    if vantar:
        t.append("## Bra matchningar som väntar på brev\n")
        t.append("Skrivs nästa körning, eller direkt med `python testa_brev.py --id <id>`.\n")
        t += [rad(s, i) + f" · id {i}" for i, s in vantar]
        t.append("")
    dubbletter = lista("dubblett")
    if dubbletter:
        t.append("## Samma annons på en annan ort\n")
        t += [rad(s, i) + f" · samma som id {s['samma_som']}" for i, s in dubbletter]
        t.append("")
    if lag:
        t.append("## Låg poäng\n")
        t += [rad(s, i, med_skal=True) for i, s in lag]
        t.append("")
    if bort:
        t.append("## Sorterades bort\n")
        t += [rad(s, i, med_skal=True) for i, s in bort]
        t.append("")
    if fel:
        t.append("## Fel (provas igen nästa körning)\n")
        t += [f"- {text}" for text in fel]
        t.append("")
    fil.write_text("\n".join(t), encoding="utf-8")
    return fil


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--utan-brev", action="store_true", help="bara sålla och poängsätta, skriv inga brev")
    p.add_argument("--dagar", type=int, help="hur många dagar bakåt att titta (standard: 3, första gången 60)")
    args = p.parse_args()

    if not PROFIL.exists():
        sys.exit(f"Hittar inte {PROFIL} – skapa den med din CV-text och dina projekt.")
    inst = las_installningar()
    profil = PROFIL.read_text(encoding="utf-8")
    prompt_matchning = (ROOT / "prompt_matchning.md").read_text(encoding="utf-8")
    prompt_brev = (ROOT / "prompt_brev.md").read_text(encoding="utf-8")
    prompt_granskning = (ROOT / "prompt_granskning.md").read_text(encoding="utf-8")
    sedda = las_sedda()
    idag = datetime.date.today().isoformat()
    fel = []

    # 1. Hämta och bedöm nya annonser
    nya = hamta_nya(inst, sedda, args.dagar)
    print(f"{len(nya)} nya annonser att bedöma …")

    def bedom_en(a):
        try:
            return a, bedom(a, prompt_matchning, profil, inst), None
        except (DeepSeekFel, ValueError, KeyError) as e:
            return a, None, e

    with ThreadPoolExecutor(max_workers=4) as ex:
        resultat = list(ex.map(bedom_en, nya))

    for a, b, e in resultat:
        if e:
            fel.append(f"{a.get('headline')} – {arbetsgivare(a)}: kunde inte bedömas ({e})")
            continue
        if b["sortera_bort"]:
            status = "bortsorterad"
        elif b["poang"] >= inst.get("min_poang", 7):
            status = "vantar"
        else:
            status = "lag_poang"
        sedda[a["id"]] = {"datum": idag, "rubrik": a.get("headline"), "arbetsgivare": arbetsgivare(a),
                          "ort": ort(a), "sista_dag": (a.get("application_deadline") or "")[:10],
                          "status": status, **b}
    spara_sedda(sedda)

    # 2. Skriv brev till de bästa matchningarna (även sådana som väntat sedan tidigare)
    brev_id = []
    if not args.utan_brev:
        vantande = [i for i, s in sedda.items()
                    if s["status"] == "vantar" and (not s["sista_dag"] or s["sista_dag"] >= idag)]
        vantande.sort(key=lambda i: (-sedda[i]["poang"], sedda[i]["sista_dag"] or "9999"))
        # Samma annons publiceras ibland för flera orter – skriv bara ett brev.
        har_brev = {(s["arbetsgivare"], s["rubrik"]): i for i, s in sedda.items() if s["status"] == "brev"}
        valda = []
        for i in vantande:
            nyckel = (sedda[i]["arbetsgivare"], sedda[i]["rubrik"])
            if nyckel in har_brev:
                sedda[i]["status"], sedda[i]["samma_som"] = "dubblett", har_brev[nyckel]
            elif len(valda) < inst.get("max_brev_per_korning", 5):
                har_brev[nyckel] = i
                valda.append(i)
        vantande = valda
        if vantande:
            print(f"Skriver {len(vantande)} brev …")
        annons_per_id = {a["id"]: a for a in nya}

        def brev_en(i):
            try:
                a = annons_per_id.get(i) or hamta_annons(i)
                fil, _, _ = skriv_och_granska(prompt_brev, prompt_granskning, profil, a,
                                              inst.get("modell_brev", "deepseek-reasoner"))
                return i, fil, None
            except (DeepSeekFel, requests.RequestException) as e:
                return i, None, e

        with ThreadPoolExecutor(max_workers=3) as ex:
            for i, fil, e in ex.map(brev_en, vantande):
                if isinstance(e, requests.HTTPError) and e.response is not None and e.response.status_code == 404:
                    sedda[i]["status"] = "borttagen"
                elif e:
                    fel.append(f"{sedda[i]['rubrik']} – {sedda[i]['arbetsgivare']}: brevet kunde inte skrivas ({e})")
                else:
                    sedda[i]["status"] = "brev"
                    sedda[i]["brev"] = fil.relative_to(PRIVAT).as_posix()
                    sedda[i]["brev_datum"] = idag
                    brev_id.append(i)
        spara_sedda(sedda)

    # 3. Rapport
    rapport = skriv_rapport(idag, sedda, fel)
    print(f"Klart: {len(brev_id)} brev skrivna. Läs {rapport.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
