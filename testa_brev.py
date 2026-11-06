#!/usr/bin/env python3
"""Testa hur bra DeepSeek skriver personliga brev.

Hämtar färska annonser från Arbetsförmedlingens JobSearch-API (Platsbanken) och
låter DeepSeek skriva ett brev per annons, utifrån din profil i private/profil.md.
Sedan granskar DeepSeek brevet mot profilen och annonsen och tar bort sådant den
hittat på. Breven sparas i private/brev/ (mappen committas aldrig).

    export DEEPSEEK_API_KEY=sk-...
    python testa_brev.py "automation Borlänge" --antal 3
    python testa_brev.py --id 29912345            # en specifik annons
"""
import argparse
import datetime
import os
import pathlib
import re
import sys

import requests

JOBSEARCH = "https://jobsearch.api.jobtechdev.se"
DEEPSEEK = "https://api.deepseek.com/chat/completions"
ROOT = pathlib.Path(__file__).resolve().parent
PROFIL = ROOT / "private" / "profil.md"
PROMPT = ROOT / "prompt_brev.md"
PROMPT_GRANSKNING = ROOT / "prompt_granskning.md"
UT = ROOT / "private" / "brev"


def hamta_annonser(sokord, antal):
    r = requests.get(f"{JOBSEARCH}/search", params={"q": sokord, "limit": antal},
                     headers={"accept": "application/json"}, timeout=30)
    r.raise_for_status()
    return r.json()["hits"]


def hamta_annons(annons_id):
    r = requests.get(f"{JOBSEARCH}/ad/{annons_id}", headers={"accept": "application/json"}, timeout=30)
    r.raise_for_status()
    return r.json()


def annons_som_text(a):
    plats = (a.get("workplace_address") or {}).get("municipality") or ""
    yrke = (a.get("occupation") or {}).get("label") or ""
    beskrivning = (a.get("description") or {}).get("text") or ""
    return (f"Rubrik: {a.get('headline')}\n"
            f"Arbetsgivare: {(a.get('employer') or {}).get('name')}\n"
            f"Ort: {plats}\nYrke: {yrke}\n\n{beskrivning}")


def fraga_deepseek(system_prompt, meddelande, modell):
    nyckel = os.environ.get("DEEPSEEK_API_KEY")
    if not nyckel:
        sys.exit("Sätt DEEPSEEK_API_KEY först (din nyckel från platform.deepseek.com).")
    r = requests.post(DEEPSEEK, timeout=300,
                      headers={"Authorization": f"Bearer {nyckel}"},
                      json={"model": modell,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": meddelande},
                            ]})
    if not r.ok:
        sys.exit(f"DeepSeek svarade {r.status_code}: {r.text[:500]}")
    return r.json()["choices"][0]["message"]["content"].strip()


def skriv_brev(system_prompt, profil, annons, modell):
    return fraga_deepseek(system_prompt,
                          f"Dagens datum: {datetime.date.today()}\n\n# PROFIL\n{profil}\n\n# ANNONS\n{annons}",
                          modell)


def granska_brev(system_prompt, profil, annons, brev, modell):
    """Returnerar (granskat brev, lista med ändringar). Vid oväntat svar: originalbrevet och None."""
    svar = fraga_deepseek(system_prompt,
                          f"Dagens datum: {datetime.date.today()}\n\n# PROFIL\n{profil}\n\n"
                          f"# ANNONS\n{annons}\n\n# BREVET\n{brev}",
                          modell)
    m = re.search(r"BREV:\s*(.*?)\s*ÄNDRINGAR:\s*(.*)", svar, re.S)
    if not m or len(m.group(1).split()) < 80:
        return brev, None
    andringar = [rad.strip() for rad in m.group(2).splitlines() if rad.strip().startswith("-")]
    andringar = [r for r in andringar if r.lstrip("- ").strip().lower() != "inga"]
    return m.group(1).strip(), andringar


def spara(a, brev, andringar=None, utkast=None):
    UT.mkdir(parents=True, exist_ok=True)
    namn = re.sub(r"[^a-z0-9]+", "-", f"{(a.get('employer') or {}).get('name', '')}".lower()).strip("-")[:40]
    fil = UT / f"{datetime.date.today()}_{a['id']}_{namn}.md"
    ansok = a.get("application_details") or {}
    text = (f"# {a.get('headline')} – {(a.get('employer') or {}).get('name')}\n\n"
            f"Annons: https://arbetsformedlingen.se/platsbanken/annonser/{a['id']}\n"
            f"Sista ansökningsdag: {a.get('application_deadline')}\n"
            f"Ansök via: {ansok.get('url') or ansok.get('email') or ansok.get('information') or 'se annonsen'}\n\n"
            f"---\n\n{brev}\n")
    if andringar is not None:
        text += "\n---\n\n## Granskningen ändrade\n\n" + ("\n".join(andringar) or "Inget.") + "\n"
    if utkast is not None and utkast != brev:
        text += f"\n## Första utkastet\n\n{utkast}\n"
    fil.write_text(text, encoding="utf-8")
    return fil


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sokord", nargs="?", default="automation Borlänge", help="fritext, t.ex. 'elektriker Falun'")
    p.add_argument("--antal", type=int, default=3)
    p.add_argument("--id", help="skriv brev till en specifik annons (id från Platsbanken-länken)")
    p.add_argument("--modell", default="deepseek-reasoner", help="deepseek-reasoner (bäst) eller deepseek-chat (snabbare)")
    p.add_argument("--utan-granskning", action="store_true", help="hoppa över faktagranskningen")
    args = p.parse_args()

    if not PROFIL.exists():
        sys.exit(f"Hittar inte {PROFIL} – skapa den med din CV-text och dina projekt.")
    profil = PROFIL.read_text(encoding="utf-8")
    prompt_brev = PROMPT.read_text(encoding="utf-8")
    prompt_granskning = PROMPT_GRANSKNING.read_text(encoding="utf-8")

    annonser = [hamta_annons(args.id)] if args.id else hamta_annonser(args.sokord, args.antal)
    if not annonser:
        sys.exit("Inga annonser hittades – prova andra sökord.")

    for a in annonser:
        print(f"\n=== {a.get('headline')} – {(a.get('employer') or {}).get('name')} ===")
        annons = annons_som_text(a)
        utkast = skriv_brev(prompt_brev, profil, annons, args.modell)
        if args.utan_granskning:
            brev, andringar = utkast, None
        else:
            brev, andringar = granska_brev(prompt_granskning, profil, annons, utkast, args.modell)
            if andringar is None:
                print("(Granskningen gav ett oväntat svar – visar första utkastet, läs extra noga.)")
        print(brev)
        if andringar:
            print("\nGranskningen ändrade:\n" + "\n".join(andringar))
        elif andringar is not None:
            print("\nGranskningen hittade inget att ändra.")
        print(f"\n(sparat i {spara(a, brev, andringar, utkast).relative_to(ROOT)})")


if __name__ == "__main__":
    main()
