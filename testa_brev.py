#!/usr/bin/env python3
"""Testa hur bra DeepSeek skriver personliga brev.

Hämtar färska annonser från Arbetsförmedlingens JobSearch-API (Platsbanken) och
låter DeepSeek skriva ett brev per annons, utifrån din profil i private/profil.md.
Breven sparas i private/brev/ (mappen committas aldrig).

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


def skriv_brev(system_prompt, profil, annons, modell):
    nyckel = os.environ.get("DEEPSEEK_API_KEY")
    if not nyckel:
        sys.exit("Sätt DEEPSEEK_API_KEY först (din nyckel från platform.deepseek.com).")
    r = requests.post(DEEPSEEK, timeout=120,
                      headers={"Authorization": f"Bearer {nyckel}"},
                      json={"model": modell,
                            "messages": [
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": f"Dagens datum: {datetime.date.today()}\n\n"
                                                                f"# PROFIL\n{profil}\n\n# ANNONS\n{annons}"},
                            ]})
    if not r.ok:
        sys.exit(f"DeepSeek svarade {r.status_code}: {r.text[:500]}")
    return r.json()["choices"][0]["message"]["content"].strip()


def spara(a, brev):
    UT.mkdir(parents=True, exist_ok=True)
    namn = re.sub(r"[^a-z0-9]+", "-", f"{(a.get('employer') or {}).get('name', '')}".lower()).strip("-")[:40]
    fil = UT / f"{datetime.date.today()}_{a['id']}_{namn}.md"
    ansok = a.get("application_details") or {}
    fil.write_text(
        f"# {a.get('headline')} – {(a.get('employer') or {}).get('name')}\n\n"
        f"Annons: https://arbetsformedlingen.se/platsbanken/annonser/{a['id']}\n"
        f"Sista ansökningsdag: {a.get('application_deadline')}\n"
        f"Ansök via: {ansok.get('url') or ansok.get('email') or ansok.get('information') or 'se annonsen'}\n\n"
        f"---\n\n{brev}\n", encoding="utf-8")
    return fil


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("sokord", nargs="?", default="automation Borlänge", help="fritext, t.ex. 'elektriker Falun'")
    p.add_argument("--antal", type=int, default=3)
    p.add_argument("--id", help="skriv brev till en specifik annons (id från Platsbanken-länken)")
    p.add_argument("--modell", default="deepseek-chat")
    args = p.parse_args()

    if not PROFIL.exists():
        sys.exit(f"Hittar inte {PROFIL} – skapa den med din CV-text och dina projekt.")
    profil = PROFIL.read_text(encoding="utf-8")
    system_prompt = PROMPT.read_text(encoding="utf-8")

    annonser = [hamta_annons(args.id)] if args.id else hamta_annonser(args.sokord, args.antal)
    if not annonser:
        sys.exit("Inga annonser hittades – prova andra sökord.")

    for a in annonser:
        print(f"\n=== {a.get('headline')} – {(a.get('employer') or {}).get('name')} ===")
        brev = skriv_brev(system_prompt, profil, annons_som_text(a), args.modell)
        print(brev)
        print(f"\n(sparat i {spara(a, brev).relative_to(ROOT)})")


if __name__ == "__main__":
    main()
