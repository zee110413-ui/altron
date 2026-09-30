**Altron 0.2.0 — only the keyboard and the mouse.** Update the brain too: run `install_altron.bat` again (or `git pull`).

- **No Baritone and no scripted routines.** The AI plays with its hands only: `control` holds keys, moves the look to
  a point or keeps the crosshair on a creature, clicks and holds the mouse buttons, picks a hotbar slot; `gui` and
  `click_slot` work any window. Walking, following, fighting, digging, building, smelting, crafting in the grid and
  driving are all done move by move by the AI.
- **The body decides nothing**: it does not fight back, eat, turn its head or close windows by itself; threats, hunger
  and death come to the AI as news.
- **One voice: Maxim**, the speech-synthesizer voice of Kava's videos, in every language — from Windows (SAPI 5) or
  from Amazon Polly with your own AWS key (the installer asks for it). No other voice: without Maxim he writes in the
  game chat. Piper is gone. The teammate manner (a much funnier, dry humour) is the default.
- `voice_preview.py` — hear the voice; the field test checks the hands on a course in the air.

**Альтрон 0.2.0 — только клавиатура и мышь.** Обнови и мозг: запусти `install_altron.bat` ещё раз (или `git pull`).

- **Без Baritone и без готовых сценариев**: нейросеть играет только руками — клавиши, взгляд, кнопки мыши, слот,
  клики в окнах. Ходит, дерётся, копает, строит, плавит, крафтит и водит технику сама, движение за движением.
- **Тело само ничего не решает**: не отбивается, не ест, не вертит головой; угроза, голод и смерть приходят нейросети
  как новости.
- **Голос один — Максим**, как у Кавы в роликах, по-русски и по-английски: из Windows (SAPI 5) или из Amazon Polly
  по твоему ключу AWS (установщик спросит). Других голосов нет: без Максима Альтрон пишет в чат игры. Piper убран.
  Манера тиммейта с очень сухим юмором — по умолчанию.
- `voice_preview.py` — послушать голос; полевой тест проверяет руки на полосе в воздухе.
