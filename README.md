\# Keshavarzyar Usability Test



A small Django site for running remote usability tests of the Keshavarzyar farm app.

Each participant opens a personal invite link, sees a task panel next to the app (in an iframe),

and the browser records their tab (and optionally the microphone) while they work through the tasks.



\*\*Stack:\*\* Django 5.2, SQLite, vanilla JavaScript, Persian RTL UI, Gunicorn, Docker.



\## What it does



\- Participants: consent page, 8 tasks with previous/next, "done" / "couldn't do it", a comment box per task, and a final 1-5 rating.

\- Recording: 10-second chunks are uploaded during the session, so little is lost if the tab closes.

\- Manager (`/manage/`): create invite links, see task results, time per task, comments and ratings, play or download recordings, export CSV.

\- Tasks are defined in `study/tasks.py`.



\## Where it runs



| Host | Purpose |

|---|---|

| `usability.keshavarz-yaar.ir` | this site (participants and `/manage/`) |

| `demo.keshavarz-yaar.ir` | throwaway copy of Keshavarzyar used only for the test (loaded in the iframe) |

| `keshavarz-yaar.ir` | the clean production app, never used for testing |



A shared Caddy in `edge/` owns ports 80/443 and routes by hostname. All containers join the Docker network `edge`.



\## Run in production (Docker)



```bash

docker network create edge              # once per server

cp .env.example .env                    # then edit it

docker compose up -d --build

```



Required `.env` values:



| Variable | Example |

|---|---|

| `SECRET\_KEY` | output of `openssl rand -hex 32` |

| `DEBUG` | `0` |

| `ALLOWED\_HOSTS` | `usability.keshavarz-yaar.ir` |

| `CSRF\_TRUSTED\_ORIGINS` | `https://usability.keshavarz-yaar.ir` |

| `APP\_URL` | `https://demo.keshavarz-yaar.ir/` |

| `MANAGER\_PASSWORD` | a strong password |



The edge proxy is started separately (`cd edge`, copy `.env.example` to `.env`, `docker compose up -d`).

The SQLite database and recordings live in the `usability\_data` volume (`/data` in the container).



Then open `https://usability.keshavarz-yaar.ir/manage/login/`, create participants and send each their link.



\## Run locally



```bash

pip install -r requirements.txt

export SECRET\_KEY=dev DEBUG=1 MANAGER\_PASSWORD=dev APP\_URL=http://localhost:8001/

python manage.py migrate

python manage.py runserver

```



Open `http://localhost:8000/manage/login/`. Screen capture works on `localhost`; elsewhere it needs HTTPS.



\## Notes



\- Use Chrome or Edge on a computer for full screen recording. Phones can only record audio.

\- Back up recordings and the CSV before removing the `usability\_data` volume.

\- Browser-recorded WebM files may show a wrong duration. Open them in VLC, or fix with `ffmpeg -i in.webm -c copy out.webm`.





\## About the recordings' format

Browsers record video as a live stream, so the file doesn't contain the video's total length or a seek map. Because of that, when you play a recording in the manager page, the length may show wrong and dragging the timeline may not work well. The video itself is complete and fine.



If you want a cleaner file for watching or sharing, there are two options:



Easiest: download the recording from /manage/ and open it with VLC. It plays and seeks fine.

Fix the file: install ffmpeg on your laptop and run this in the folder with the file:

ffmpeg -i in.webm -c copy out.webm

\-i in.webm is the downloaded file (use its real name).

\-c copy means "don't re-encode". It's quick and loses no quality.

out.webm is the repaired copy.



On Windows you can install it with winget install ffmpeg in PowerShell.

