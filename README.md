# jobb

Finds jobs on Platsbanken (Arbetsförmedlingen), drops the ones you can't get, and writes a tailored personligt brev for the best matches with DeepSeek. You read every letter and send it yourself.

## Setup

1. Put your CV text and your own projects in `private/profil.md`. The more concrete details, the better the letters. The `private/` folder is git-ignored, because this repo is public.
2. Get an API key at <https://platform.deepseek.com> and put it in `private/deepseek_nyckel.txt` (or set `DEEPSEEK_API_KEY`).
3. `pip install requests` (Python 3.11 or newer).

## Every day: `daglig.py`

```bash
python daglig.py
```

1. Fetches new ads in your county and job areas. The first run looks at every open ad; later runs look 3 days back and skip ads already judged (`private/sedda.json`).
2. Drops ads that require a licence you don't have (straight from the ad's data).
3. A fast model (`deepseek-chat`) drops ads that require years of experience, certificates, a degree, or a workplace outside your area, and scores the rest 1–10. If it drops an ad that still scored 5 or higher, the careful model (`deepseek-reasoner`) judges it again, so good jobs aren't lost by mistake.
4. Writes letters for up to 5 of the best matches (7/10 or higher), most urgent deadline first. The same ad posted for several towns gets one letter. The rest wait for the next run.
5. Writes today's list to `private/listor/<date>.md`: letters ready to read, matches waiting, low scores, and what was dropped and why. Glance at the dropped list now and then to check the filter isn't too strict.

The first run creates `private/installningar.toml`. Change the county, job areas, licences, wishes, score limit and letters per run there.

`python daglig.py --utan-brev` only sorts and scores.

### Run it automatically

Linux/macOS, every morning at 07:10 (`crontab -e`):

```
10 7 * * * cd ~/jobb && python3 daglig.py >> private/logg.txt 2>&1
```

Windows: Task Scheduler → Create Basic Task → Daily → program `python`, arguments `daglig.py`, start in your `jobb` folder.

## One ad at a time: `testa_brev.py`

```bash
python testa_brev.py --id 31591355        # the number in the Platsbanken link
python testa_brev.py "automation Borlänge" --antal 3
```

## How a letter is written

First DeepSeek writes a draft (`prompt_brev.md`). Then a second call checks it against your profile and the ad (`prompt_granskning.md`). It removes anything made up, removes agreeing to on-call duty or travel, corrects mixed-up project details, and fixes spelling. The list of changes and the first draft are saved under each letter. Sorting rules are in `prompt_matchning.md`.
