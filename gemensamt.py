"""Delade funktioner: Platsbanken (JobSearch-API:t), DeepSeek och att spara brev."""
import datetime
import os
import pathlib
import re

import requests

JOBSEARCH = "https://jobsearch.api.jobtechdev.se"
DEEPSEEK = "https://api.deepseek.com/chat/completions"
ROOT = pathlib.Path(__file__).resolve().parent
PRIVAT = ROOT / "private"
PROFIL = PRIVAT / "profil.md"
BREV_MAPP = PRIVAT / "brev"


class DeepSeekFel(Exception):
    pass


# --- Platsbanken -------------------------------------------------------------

def sok_annonser(params, max_antal=2000):
    """Hämtar alla träffar för en sökning (API:t ger max 100 per anrop)."""
    annonser, offset = [], 0
    while offset < max_antal:
        r = requests.get(f"{JOBSEARCH}/search", params={**params, "limit": 100, "offset": offset},
                         headers={"accept": "application/json"}, timeout=30)
        r.raise_for_status()
        traffar = r.json()["hits"]
        annonser += traffar
        if len(traffar) < 100:
            break
        offset += 100
    return annonser


def hamta_annons(annons_id):
    r = requests.get(f"{JOBSEARCH}/ad/{annons_id}", headers={"accept": "application/json"}, timeout=30)
    r.raise_for_status()
    return r.json()


def arbetsgivare(a):
    return (a.get("employer") or {}).get("name") or ""


def ort(a):
    return (a.get("workplace_address") or {}).get("municipality") or ""


def lank(a):
    return f"https://arbetsformedlingen.se/platsbanken/annonser/{a['id']}"


def korkort_som_krävs(a):
    if not a.get("driving_license_required"):
        return []
    return [k.get("label") for k in (a.get("driving_license") or []) if k.get("label")]


def annons_som_text(a):
    def etikett(falt):
        return (a.get(falt) or {}).get("label") or "–"
    korkort = ", ".join(korkort_som_krävs(a)) or "Nej"
    return (f"Rubrik: {a.get('headline')}\n"
            f"Arbetsgivare: {arbetsgivare(a)}\n"
            f"Ort: {ort(a)}\n"
            f"Yrke: {etikett('occupation')}\n"
            f"Omfattning: {etikett('working_hours_type')}, {etikett('employment_type')}, {etikett('duration')}\n"
            f"Körkort krävs: {korkort}\n"
            f"Arbetsgivaren har kryssat i 'Erfarenhet krävs': {'Ja' if a.get('experience_required') else 'Nej'}\n"
            f"Sista ansökningsdag: {(a.get('application_deadline') or '')[:10]}\n\n"
            f"{(a.get('description') or {}).get('text') or ''}")


# --- DeepSeek ----------------------------------------------------------------

def fraga_deepseek(system_prompt, meddelande, modell, json_svar=False, temperatur=None):
    nyckelfil = PRIVAT / "deepseek_nyckel.txt"
    nyckel = os.environ.get("DEEPSEEK_API_KEY") or (nyckelfil.exists() and nyckelfil.read_text().strip())
    if not nyckel:
        raise DeepSeekFel("Hittar ingen DeepSeek-nyckel. Lägg den i private/deepseek_nyckel.txt "
                          "eller sätt DEEPSEEK_API_KEY (nyckeln får du på platform.deepseek.com).")
    kropp = {"model": modell,
             "messages": [{"role": "system", "content": system_prompt},
                          {"role": "user", "content": meddelande}]}
    if json_svar:
        kropp["response_format"] = {"type": "json_object"}
    if temperatur is not None:
        kropp["temperature"] = temperatur
    try:
        r = requests.post(DEEPSEEK, timeout=300, headers={"Authorization": f"Bearer {nyckel}"}, json=kropp)
    except requests.RequestException as e:
        raise DeepSeekFel(f"Kunde inte nå DeepSeek: {e}") from e
    if not r.ok:
        raise DeepSeekFel(f"DeepSeek svarade {r.status_code}: {r.text[:300]}")
    return r.json()["choices"][0]["message"]["content"].strip()


def skriv_brev(prompt, profil, annons, modell):
    return fraga_deepseek(prompt,
                          f"Dagens datum: {datetime.date.today()}\n\n# PROFIL\n{profil}\n\n# ANNONS\n{annons}",
                          modell)


def granska_brev(prompt, profil, annons, brev, modell):
    """Returnerar (granskat brev, lista med ändringar). Vid oväntat svar: originalbrevet och None."""
    svar = fraga_deepseek(prompt,
                          f"Dagens datum: {datetime.date.today()}\n\n# PROFIL\n{profil}\n\n"
                          f"# ANNONS\n{annons}\n\n# BREVET\n{brev}",
                          modell)
    m = re.search(r"BREV:\s*(.*?)\s*ÄNDRINGAR:\s*(.*)", svar, re.S)
    if not m or len(m.group(1).split()) < 80:
        return brev, None
    andringar = [rad.strip() for rad in m.group(2).splitlines() if rad.strip().startswith("-")]
    andringar = [r for r in andringar if r.lstrip("- ").strip().lower() != "inga"]
    return m.group(1).strip(), andringar


def skriv_och_granska(prompt_brev, prompt_granskning, profil, a, modell, granska=True):
    """Skriver ett brev, granskar det och sparar. Returnerar (fil, brev, ändringar)."""
    annons = annons_som_text(a)
    utkast = skriv_brev(prompt_brev, profil, annons, modell)
    brev, andringar = utkast, None
    if granska:
        brev, andringar = granska_brev(prompt_granskning, profil, annons, utkast, modell)
    return spara_brev(a, brev, andringar, utkast), brev, andringar


def spara_brev(a, brev, andringar=None, utkast=None):
    BREV_MAPP.mkdir(parents=True, exist_ok=True)
    namn = re.sub(r"[^a-z0-9]+", "-", arbetsgivare(a).lower()).strip("-")[:40]
    fil = BREV_MAPP / f"{datetime.date.today()}_{a['id']}_{namn}.md"
    ansok = a.get("application_details") or {}
    text = (f"# {a.get('headline')} – {arbetsgivare(a)}\n\n"
            f"Annons: {lank(a)}\n"
            f"Sista ansökningsdag: {(a.get('application_deadline') or '')[:10]}\n"
            f"Ansök via: {ansok.get('url') or ansok.get('email') or ansok.get('information') or 'se annonsen'}\n\n"
            f"---\n\n{brev}\n")
    if andringar is not None:
        text += "\n---\n\n## Granskningen ändrade\n\n" + ("\n".join(andringar) or "Inget.") + "\n"
    if utkast is not None and utkast != brev:
        text += f"\n## Första utkastet\n\n{utkast}\n"
    fil.write_text(text, encoding="utf-8")
    return fil
