# Altron — a voice AI companion for modded Minecraft

Altron is a second player in your world that you talk to by voice. He follows you, fights, mines, crafts, carries
things between chests and machines, uses the GUIs of any mod, remembers your base, chats with you — and answers
in your language.

It runs **fully offline on your PC**: a local LLM (llama.cpp), Whisper for speech recognition and Piper for the voice.

> Altron is a fan project and is not affiliated with Marvel or Disney. The "ultron" voice style is a synthetic
> audio effect applied to any Piper voice — it is not a copy of any actor's voice.

*Русская версия — ниже.*

## What he can do

- **Talk**: in any language Whisper and your Piper voices know. He answers in the language you speak, jokes, remembers
  what you tell him about yourself, and starts small talk when it has been quiet for a while (`chatter_minutes`).
- **Work with any mod** (Forge 1.20.1 packs): reads every mod's items, recipes and manuals from the pack's files,
  opens and clicks any mod window, uses machines he has seen to make things (`obtain`), loads materials into a machine
  in one trip (`load_machine`), looks things up on the web when the pack's data is not enough.
- **Play**: follow, guard, fight (bows, swords and mod guns), mine like a player (no x-ray), smelt, craft, build
  multiblocks, drive vehicles, climb ladders, fetch and stash items, study your base, sleep in a bed at night, gesture
  (nod, wave, bow, dance), set reminders, pick his things up after he dies.
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
  - `mod/libs/baritone-api-forge-1.10.1.jar` — Baritone for Forge 1.20.1.

## Setup

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

## Settings (`brain/config.json`)

| key | meaning |
| --- | --- |
| `language` | `"auto"` (answers in the language you speak) or a fixed code: `"en"`, `"ru"`, `"de"`... |
| `languages` | languages to recognize with `"auto"`, e.g. `["en", "de"]` |
| `tts_voices` | a Piper voice per language: `{"en": "../models/piper/en_US-ryan-high.onnx"}`; `tts_voice` is the fallback |
| `tts_style` | `"ultron"` (low, doubled, metallic), `"robot"` (light helmet effect) or `"plain"`; `tts_pitch` overrides the pitch |
| `chatter_minutes` | after this many quiet minutes he may start a conversation; `0` turns it off |
| `wake_words` | what he answers to |
| `minecraft_dir`, `pack_version`, `java` | empty = found automatically |

## License

Apache License 2.0 — see [LICENSE](LICENSE).

---

# Альтрон — голосовой ИИ-напарник для Minecraft с модами

Второй игрок в твоём мире, с которым ты говоришь голосом. Ходит за тобой, воюет, копает, крафтит, носит вещи
между сундуками и машинами, пользуется окнами любых модов, помнит твою базу, болтает с тобой — на твоём языке.
Всё работает **офлайн на твоём ПК**: локальная нейросеть (llama.cpp), Whisper для распознавания речи и Piper для голоса.

> Проект фанатский и не связан с Marvel/Disney. Стиль голоса «ultron» — это звуковой эффект поверх любого голоса
> Piper, а не копия голоса какого-либо актёра.

## Что умеет

- **Общаться** на любом языке, который знают Whisper и твои голоса Piper: отвечает на языке, на котором ты говоришь,
  шутит, помнит, что ты о себе рассказывал, сам заговаривает, если долго тихо (`chatter_minutes`).
- **Работать с любыми модами** (сборки Forge 1.20.1): читает предметы, рецепты и руководства всех модов из файлов
  сборки, открывает и нажимает окна любых модов, делает предметы в увиденных машинах (`obtain`), загружает материал
  в машину за один поход (`load_machine`), ищет в интернете, если в сборке нет ответа.
- **Играть**: следовать, охранять, воевать (лук, меч, оружие модов), копать как игрок (без рентгена), плавить,
  крафтить, строить многоблочные машины, водить технику, лазить по лестницам, приносить и складывать вещи, изучать
  базу, спать ночью в кровати, делать жесты (кивнуть, помахать, поклониться, станцевать), напоминать, подбирать свои
  вещи после смерти.
- **Помнить** места, сундуки и их содержимое, твои слова — и после перезапуска.

## Установка

1. Скачай нужные файлы (llama-server, модель GGUF, модели Whisper, голоса Piper, Baritone) в `tools/`, `models/`,
   `mod/libs/` — список выше, в разделе *Requirements*. Проверь пути в `brain/config.json`.
2. `cd brain`, `python -m venv .venv`, `.venv\Scripts\pip install -r requirements.txt`.
3. Собери мод: `build_mod.bat` (нужна Java 17 — `JAVA_HOME` или папка `tools\jdk-17*`).
4. Запусти мозг: `start_altron.bat`. В первый раз он спросит, какую сборку взять — подойдёт любая сборка
   Forge 1.20.1 из `.minecraft/versions`. Дождись строки «ИИ готов».
5. Запусти эту же сборку, зайди в мир и напиши в чате `/altron`. Через 1-2 минуты Альтрон зайдёт.
6. Говори в голосовом чате: «Альтрон, иди за мной».

Настройки — в таблице выше (`language`, `languages`, `tts_voices`, `tts_style`, `chatter_minutes`...).
Подробная инструкция для игры — в `КАК ИГРАТЬ.txt`.

## Лицензия

Apache License 2.0 — см. [LICENSE](LICENSE).
