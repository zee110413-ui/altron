# Altron — a voice AI companion for modded Minecraft

Altron is a second player in your world that you talk to by voice. He follows you, fights, mines, crafts, carries
things between chests and machines, uses the GUIs of any mod, remembers your base, chats with you — and answers
in your language. The code gives him senses and skills; **every decision — what to do, what to say, or to keep quiet
— is made by the AI itself**.

It runs **fully offline on your PC**: a local LLM (llama.cpp), Whisper for speech recognition and Piper for the voice.

**Website:** https://zee110413-ui.github.io/altron/

> Altron is a fan project and is not affiliated with Marvel or Disney. The "ultron" voice style is a synthetic
> audio effect applied to any Piper voice — it is not a copy of any actor's voice.

*Русская версия — ниже.*

## What he can do

- **Talk**: in any language Whisper and your Piper voices know. He answers in the language you speak (and thinks in
  it: Russian or English instructions), in his own words — nothing he says is canned, except a shout when a creeper
  is about to blow up. He starts speaking while the AI is still writing the answer, stops when you talk over him.
- **Feel and remember**: he has a mood of his own that you hear in his voice, an attitude to every player built up by
  what they did (saved him, gave him diamonds, hit him), opinions and tastes he keeps, and shared moments he brings up
  later ("remember when the creeper took our first house?"). In quiet moments he decides himself whether to say
  something, ask, joke or stay silent.
- **Two voices**: `altron` — a cold, theatrical machine; `teammate` — a deadpan raid teammate with a flat
  speech-synthesizer voice and dry humour. Say "talk like the teammate" to switch.
- **Notice what happens**: dangers, players badly hurt or downed, deaths, advancements, players joining, night and
  storms reach the AI as facts, and it decides what to do about them.
- **Several players**: obeys the commander and his friends ("Vasya is my friend, obey him"), talks to strangers but
  does not take their orders, remembers what each player tells about themselves.
- **Goals of his own**: long orders ("guard the base", "help me", "live on your own") become his goals; while he has
  them he keeps looking around and decides what to do. When he dies, getting his things back becomes a goal too.
- **Learn**: every turn of the AI is logged; say "well done" or "not like that" and it is rated. The good turns
  fine-tune the same model — see [TRAINING.md](TRAINING.md).
- **Work with any mod** (Forge 1.20.1 packs): reads every mod's items, recipes and manuals from the pack's files,
  opens and clicks any mod window, uses machines he has seen to make things (`obtain`), loads materials into a machine
  in one trip (`load_machine`), looks things up on the web when the pack's data is not enough.
- **Play**: walk with his own pathfinding (steps, jumps, drops, ladders, swimming, doors), follow, guard, fight (bows,
  swords and mod guns), mine like a player (no x-ray), smelt, craft, build multiblocks, drive vehicles and man their
  guns, get downed players up, fetch and stash items, study your base, sleep in a bed at night, gesture (nod, wave,
  bow, dance), set reminders, build a house, shelter, wall, tower, platform or bridge from a description.
- **Remember**: places, chests and their contents, what you said — across restarts.

## Requirements

- Windows 10/11 (the brain also runs on Linux/macOS, the helper `.bat` files are for Windows), an NVIDIA GPU with
  8+ GB is recommended.
- A **Forge 1.20.1** modpack with **Simple Voice Chat** installed.
- Python 3.11+, Java 17 (JDK, only to build the mod).
- Files you download yourself (not in this repository):
  - `tools/llama/llama-server.exe` — [llama.cpp](https://github.com/ggml-org/llama.cpp) server;
  - `models/*.gguf` — a chat model with tool calling (e.g. a Qwen 7-9B GGUF) and, for vision, its `mmproj` file;
  - `models/whisper-large-v3-turbo` (and `whisper-small` for CPU) — faster-whisper models;
  - `models/piper/*.onnx` — [Piper voices](https://huggingface.co/rhasspy/piper-voices), one per language;
  - `mod/libs/baritone-api-forge-1.10.1.jar` — Baritone for Forge 1.20.1: mining and building by blueprint (walking
    works without it; with it installed it is also a backup when his own legs find no way).

## Quick install (Windows)

Download **[install_altron.bat](https://github.com/zee110413-ui/altron/raw/main/install_altron.bat)** and double-click it.
It installs Altron into `%USERPROFILE%\Altron` and downloads everything by itself: Python and packages, the llama.cpp
server (CUDA, Vulkan or CPU build for your PC), the AI model, Whisper models, Piper voices, Baritone, Java 17 and
Simple Voice Chat for your modpack; then it builds the mod and puts an "Altron" shortcut on the desktop.
It is safe to run again: what is there is skipped, broken downloads continue. Run it from an existing Altron folder
to update it — your `config.json` is kept.

## Manual setup

```
git clone https://github.com/zee110413-ui/altron
cd altron/brain
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

1. Put the downloaded files into `tools/` and `models/` as listed above and check the paths in `brain/config.json`.
2. Build the mod: `build_mod.bat` (uses `JAVA_HOME`, or `tools\jdk-17*` if present).
3. Start the brain: `start_altron.bat` (or `start_altron_eco.bat` / `_balanced` / `_max`). The first time it asks which
   modpack to use — any Forge 1.20.1 pack in your `.minecraft/versions` works. Wait for "ИИ готов".
4. Start the same modpack, open your world and type `/altron` in chat. Altron joins in 1-2 minutes.
5. Talk in voice chat: "Altron, follow me".

On someone else's server: the host opens the world with `/altron lan` (or the server has `online-mode=false`), and
you enter its address when the brain asks.

## A second PC for the AI

If one PC cannot run the game, Altron's body and the AI at once, put the AI and the body on a second PC:
run `brain\.venv\Scripts\python.exe brain\build_pc2_kit.py` — it builds the `ai_server` folder (portable Python,
the brain, models, llama.cpp, the body's Minecraft files). Copy that folder to the second PC and start it there;
on your gaming PC just play and type `/altron`.

## Settings (`brain/config.json`)

| key | meaning |
| --- | --- |
| `language` | `"auto"` (answers in the language you speak) or a fixed code: `"en"`, `"ru"`, `"de"`... |
| `languages` | languages to recognize with `"auto"`, e.g. `["en", "de"]` |
| `tts_voices` | a Piper voice per language: `{"en": "../models/piper/en_US-ryan-high.onnx"}`; `tts_voice` is the fallback |
| `tts_style` | Altron's own voice: `"ultron"` (low, doubled, metallic), `"robot"` (light helmet effect) or `"plain"`; `tts_pitch` overrides the pitch |
| `persona` | `"altron"` or `"teammate"` (also switched by voice); `tts_personas` — the teammate's own voice files |
| `idle_think_minutes` | in a quiet moment this often he thinks whether to say something; `0` — only when spoken to |
| `legs` | `"own"` — his own pathfinding, Baritone as a backup (default); `"own_only"`; `"baritone"` — the old walking |
| `instant_ack` | a canned "Yes, commander" before the AI has thought (off: he answers in his own words) |
| `dataset` | log every AI turn for fine-tuning (see TRAINING.md) |
| `tts_moods` | the voice follows the moment (faster in a fight, quieter when sympathising) |
| `barge_in` | you can talk over him and he stops (default on) |
| `react_events` | reactions to what happens around (danger, deaths, players joining...) |
| `assist`, `autonomy` | help without orders / find work by himself (also switched by voice) |
| `friends` | players whose orders he also carries out (also added by voice) |
| `llm_url`, `llm_model_name`, `llm_api_key` | use an OpenAI-compatible online AI instead of a local model — for PCs without a strong video card |
| `wake_words` | what he answers to |
| `minecraft_dir`, `pack_version`, `java` | empty = found automatically |

## License

Apache License 2.0 — see [LICENSE](LICENSE).

---

# Альтрон — голосовой ИИ-напарник для Minecraft с модами

Второй игрок в твоём мире, с которым ты говоришь голосом. Ходит за тобой, воюет, копает, крафтит, носит вещи
между сундуками и машинами, пользуется окнами любых модов, помнит твою базу, болтает с тобой — на твоём языке.
Код даёт ему органы чувств и умения, а **все решения — что делать, что сказать или промолчать — принимает сама
нейросеть**.
Всё работает **офлайн на твоём ПК**: локальная нейросеть (llama.cpp), Whisper для распознавания речи и Piper для голоса.

> Проект фанатский и не связан с Marvel/Disney. Стиль голоса «ultron» — это звуковой эффект поверх любого голоса
> Piper, а не копия голоса какого-либо актёра.

## Что умеет

- **Общаться** на любом языке, который знают Whisper и твои голоса Piper: отвечает на языке, на котором ты говоришь
  (и думает на нём), своими словами — заготовленных фраз нет, кроме крика «крипер рядом!». Начинает говорить, пока
  ИИ ещё дописывает ответ, замолкает, если ты его перебил.
- **Чувствовать и помнить**: у него своё настроение (его слышно в голосе), своё отношение к каждому игроку — копится
  от поступков (спас, подарил алмазы, ударил), свои вкусы и мнения, общие воспоминания, которые он вспоминает к месту
  («помнишь, как крипер снёс наш первый дом?»). В тишине сам решает — заговорить, спросить, пошутить или промолчать.
- **Два голоса**: `altron` — холодная театральная машина; `teammate` — невозмутимый тиммейт с ровным голосом
  синтезатора речи и сухим юмором. Скажи «говори как тиммейт», чтобы сменить.
- **Замечать, что происходит**: опасность, раненые и упавшие игроки, смерти, достижения, зашедшие игроки, ночь и
  гроза приходят нейросети как факты, а что с ними делать — решает она.
- **Играть с несколькими игроками**: слушается командира и его друзей, с чужими говорит, но их приказы не выполняет,
  помнит, что каждый о себе рассказывал.
- **Свои цели**: долгие приказы («охраняй базу», «помогай мне», «живи сам») становятся его целями; пока они есть, он
  оглядывается и сам решает, что делать. Погиб — вернуть свои вещи тоже становится целью.
- **Учиться**: каждый ход нейросети записывается, «молодец» и «не так» — оценки; на хороших ходах та же модель
  дообучается — см. [TRAINING.md](TRAINING.md).
- **Работать с любыми модами** (сборки Forge 1.20.1): читает предметы, рецепты и руководства всех модов из файлов
  сборки, открывает и нажимает окна любых модов, делает предметы в увиденных машинах (`obtain`), загружает материал
  в машину за один поход (`load_machine`), ищет в интернете, если в сборке нет ответа.
- **Играть**: ходить своими ногами (свой поиск пути: шаги, прыжки, спуски, лестницы, вода, двери), следовать,
  охранять, воевать (лук, меч, оружие модов), копать как игрок (без рентгена), плавить, крафтить, строить многоблочные
  машины, водить технику и стрелять из неё, поднимать раненых, приносить и складывать вещи, изучать базу, спать ночью
  в кровати, делать жесты, напоминать, строить по описанию дом, укрытие, стену, башню, площадку или мост.
- **Помнить** места, сундуки и их содержимое, твои слова — и после перезапуска.

## Установка в один клик (Windows)

Скачай **[install_altron.bat](https://github.com/zee110413-ui/altron/raw/main/install_altron.bat)** и запусти двойным
кликом. Он поставит Альтрона в `%USERPROFILE%\Altron` и сам скачает всё нужное: Python и пакеты, сервер llama.cpp
(под твою видеокарту), модель ИИ, Whisper, голоса Piper, Baritone, Java 17 и Simple Voice Chat в твою сборку, соберёт
мод и сделает ярлык «Altron» на рабочем столе. Можно запускать повторно — готовое пропускается, оборванные загрузки
докачиваются. Запуск из существующей папки Альтрона обновляет её, твой `config.json` сохраняется.

## Установка вручную

1. Скачай нужные файлы (llama-server, модель GGUF, модели Whisper, голоса Piper, Baritone) в `tools/`, `models/`,
   `mod/libs/` — список выше, в разделе *Requirements*. Проверь пути в `brain/config.json`.
2. `cd brain`, `python -m venv .venv`, `.venv\Scripts\pip install -r requirements.txt`.
3. Собери мод: `build_mod.bat` (нужна Java 17 — `JAVA_HOME` или папка `tools\jdk-17*`).
4. Запусти мозг: `start_altron.bat`. В первый раз он спросит, какую сборку взять — подойдёт любая сборка
   Forge 1.20.1 из `.minecraft/versions`. Дождись строки «ИИ готов».
5. Запусти эту же сборку, зайди в мир и напиши в чате `/altron`. Через 1-2 минуты Альтрон зайдёт.
6. Говори в голосовом чате: «Альтрон, иди за мной».

**Слабый ПК без видеокарты?** Укажи в `config.json` адрес любой OpenAI-совместимой нейросети в интернете (`llm_url`,
`llm_model_name`, `llm_api_key`) — локальная модель тогда не нужна.

**Второй ПК.** Если твой компьютер не тянет две игры и нейросеть сразу: запусти
`brain\.venv\Scripts\python.exe brain\build_pc2_kit.py` — он соберёт папку `ai_server` (портативный Python, мозг,
модели, llama.cpp, файлы игры для тела Альтрона). Скопируй её на второй ПК и запускай там, а на своём просто играй.

Настройки — в таблице выше (`language`, `languages`, `tts_voices`, `persona`, `legs`, `idle_think_minutes`...).
Baritone нужен только для копания и стройки по чертежу: ходит Альтрон своими ногами.
Подробная инструкция для игры — в `КАК ИГРАТЬ.txt`.

## Лицензия

Apache License 2.0 — см. [LICENSE](LICENSE).
