#!/usr/bin/env python3
"""Skriv brev till en eller några annonser direkt (utan matchning).

Hämtar annonser från Platsbanken och låter DeepSeek skriva ett brev per annons
utifrån private/profil.md. Sedan granskar DeepSeek brevet mot profilen och annonsen
och tar bort sådant den hittat på. Breven sparas i private/brev/.

    export DEEPSEEK_API_KEY=sk-...
    python testa_brev.py "automation Borlänge" --antal 3
    python testa_brev.py --id 29912345            # en specifik annons
"""
import argparse
import sys

from gemensamt import PROFIL, ROOT, DeepSeekFel, arbetsgivare, hamta_annons, skriv_och_granska, sok_annonser


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
    prompt_brev = (ROOT / "prompt_brev.md").read_text(encoding="utf-8")
    prompt_granskning = (ROOT / "prompt_granskning.md").read_text(encoding="utf-8")

    annonser = [hamta_annons(args.id)] if args.id else sok_annonser({"q": args.sokord}, max_antal=args.antal)[:args.antal]
    if not annonser:
        sys.exit("Inga annonser hittades – prova andra sökord.")

    for a in annonser:
        print(f"\n=== {a.get('headline')} – {arbetsgivare(a)} ===")
        try:
            fil, brev, andringar = skriv_och_granska(prompt_brev, prompt_granskning, profil, a, args.modell,
                                                     granska=not args.utan_granskning)
        except DeepSeekFel as e:
            sys.exit(str(e))
        if not args.utan_granskning and andringar is None:
            print("(Granskningen gav ett oväntat svar – visar första utkastet, läs extra noga.)")
        print(brev)
        if andringar:
            print("\nGranskningen ändrade:\n" + "\n".join(andringar))
        elif andringar is not None:
            print("\nGranskningen hittade inget att ändra.")
        print(f"\n(sparat i {fil.relative_to(ROOT)})")


if __name__ == "__main__":
    main()
