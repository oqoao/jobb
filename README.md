# jobb

Helps find jobs on Platsbanken (Arbetsförmedlingen) and writes a tailored personligt brev for each ad with DeepSeek. You review every letter and send it yourself.

## Test the letters

1. Put your CV text and your own projects in `private/profil.md`. The more concrete details, the better the letters. The `private/` folder is git-ignored, because this repo is public.
2. Get an API key at <https://platform.deepseek.com>.
3. Run:

```bash
pip install requests
export DEEPSEEK_API_KEY=sk-...
python testa_brev.py "automation Borlänge" --antal 3
python testa_brev.py --id 31591355        # one specific ad (the number in the Platsbanken link)
```

Letters are printed and saved to `private/brev/`. To change how the letters are written, edit `prompt_brev.md`.
