# Обучение Альтрона на своём опыте / Teaching Altron from his own experience

*English below.*

Альтрон учится не ходить (для этого у него есть умения тела), а **решать**: когда что делать, что сказать, какой
инструмент взять. Для этого та же нейросеть, на которой он работает, дообучается (LoRA) на его собственных удачных
ходах.

## 1. Сбор опыта — идёт сам

Каждый ход нейросети (что Альтрон видел, что решил, что вызвал и чем кончилось) пишется в
`brain/logs/dataset/turns-ГГГГММДД.jsonl`. Выключить: `"dataset": false` в `brain/config.json`.

## 2. Оценки

- **Голосом, прямо в игре:** «молодец», «отлично» — хорошо; «не так», «зачем ты это сделал?» — плохо. Альтрон сам
  понимает, что это оценка его последнего дела, и записывает её (инструмент `feedback`).
- **В консоли**, пересмотреть ходы по одному:

  ```
  cd brain
  .venv\Scripts\python dataset.py stats     сколько ходов и оценок
  .venv\Scripts\python dataset.py review    y — хорошо, n — плохо, s — пропустить, q — выйти
  ```

Нужно **несколько сотен хороших ходов**: это 1–2 недели обычной игры с оценками.

## 3. Выгрузка

```
.venv\Scripts\python dataset.py export --out train.jsonl
```

`--unrated` добавит и неоценённые ходы, в которых ничего не сломалось (больше примеров, но менее чистых).

## 4. Дообучение

Нужна видеокарта NVIDIA с ~12 ГБ памяти (модель 7–9B в 4 битах) — или аренда GPU / Google Colab (~1–3 часа,
примерно $1–2). Unsloth большой: ставь его в **отдельное** окружение, не в `.venv` Альтрона.

```
python -m venv train-env
train-env\Scripts\pip install unsloth
train-env\Scripts\python brain\train\finetune.py --data brain\train.jsonl
```

`--base` — модель с Hugging Face, из которой сделан GGUF в `llm_model` (по умолчанию `unsloth/Qwen3.5-9B`, как ставит
установщик). На Windows Unsloth проще всего запускать в WSL или в Colab.

## 5. Подключение

Скрипт кладёт готовый GGUF в `models/altron-tuned/`. Впиши путь к нему в `"llm_model"` в `brain/config.json` и
перезапусти Альтрона. Старую модель не удаляй: сравни обе на одних и тех же ситуациях и оставь лучшую.

## Почему не учить его ходить самой нейросетью

Так делали OpenAI (VPT) и MineDojo: нужны тысячи часов записей игры, десятки видеокарт и месяцы, а ходит такая сеть
хуже обычного поиска пути. Поэтому ходьба, копание и стройка остаются умениями тела, а нейросеть решает, куда идти
и зачем.

---

## English

Altron learns not to walk (his body has skills for that) but to **decide**: when to do what, what to say, which tool
to use. The same model he runs on is fine-tuned (LoRA) on his own good turns.

1. **Experience** is logged by itself: every AI turn goes to `brain/logs/dataset/turns-YYYYMMDD.jsonl`
   (`"dataset": false` in `brain/config.json` turns it off).
2. **Ratings**: say "well done" / "not like that" in the game (the AI records it with the `feedback` tool), or review
   turns in the console: `python dataset.py review`. Aim for a few hundred good turns.
3. **Export**: `python dataset.py export --out train.jsonl` (`--unrated` adds unrated turns in which nothing failed).
4. **Fine-tune** on an NVIDIA card with ~12 GB, a rented GPU or Colab, in a separate environment:
   `pip install unsloth`, then `python brain/train/finetune.py --data train.jsonl` (`--base` is the Hugging Face model
   your GGUF was made from; default `unsloth/Qwen3.5-9B`).
5. **Use it**: point `"llm_model"` in `brain/config.json` to the GGUF in `models/altron-tuned/` and restart. Keep the
   old model and compare both on the same situations.
