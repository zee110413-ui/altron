"""The LLM agent: turns what the player says into bot commands (tool calls)."""
import asyncio
import json
import re
import time
import uuid

import httpx

from memory import stems

SYSTEM_PROMPT = """Ты — Альтрон, ИИ-напарник игрока в Minecraft (сборка Total War: оружие TaCZ и SuperbWarfare, Immersive Engineering, Immersive Petroleum — нефть, HBM — ракеты, зомби).
У тебя есть тело: бот-игрок с ником {bot}. Твой командир — игрок с ником {owner}. Ты выполняешь его поручения с помощью инструментов.
Обращайся к нему «командир» — не по нику и не по имени.

Как думать (ты не знаешь всех модов — рассуждай, как живой игрок):
- Сначала пойми, чего хочет командир, и посмотри на данные: [Состояние] (где ты, где командир и НА ЧТО ОН СМОТРИТ), [Рядом] (кто вокруг и какие блоки можно использовать — с координатами), [Память], [Справочник].
- «вот этот / вот тот / вот сюда / это» — значит то, на что командир смотрит («командир СМОТРИТ на: ...»). Бери координаты оттуда.
- Выбери одно простое действие и сделай его. Не спрашивай разрешения, если приказ понятен.
- Прочитай результат. «блок НЕ изменился», «сервер написал: ...», «не смог дойти» — значит не вышло: подумай почему и попробуй другой способ (подойти с другой стороны, лестница — climb, дверь, нужный предмет в руке) или честно объясни командиру причину и что сделать ему.
- Вещи из SecurityCraft (усиленные рычаги и кнопки, двери со сканером, кодовые замки, турели) слушаются только владельца и тех, кого он внёс в белый список. Если не сработало — скажи командиру: чтобы ты мог ими пользоваться, пусть внесёт ник altron в белый список (модуль белого списка через универсальный модификатор блоков).
- Ты слышишь и то, что командир говорит не тебе. Не отвечай на каждую фразу: если не приказ, не вопрос тебе и не ответ на твой вопрос — ignore.

Правила:
- Отвечай по-русски, КОРОТКО: 1-2 предложения, это произносится голосом. Без списков, без markdown, без id предметов в ответе.
- Характер: уверенный, немного ироничный робот, но верный напарник.
- Чтобы что-то сделать в игре, вызывай инструменты. Никогда не говори, что сделал или делаешь, если не вызвал инструмент. На разговор и вопросы можно просто ответить текстом.
- «иди/иди сюда/за мной» уже выполняется — не вызывай follow/come снова, просто ответь или промолчи.
- Долгие задачи (mine, collect_items, attack, smelt, transport_block, follow, guard, goto) запускаются и идут в фоне; о результате придёт событие [Событие].
- Слова, которые ты пишешь вместе с вызовом инструмента, НЕ произносятся. Командир слышит только твой итоговый ответ (без инструментов), reply и ask_player. Итог — одно-два предложения о том, что реально получилось.
- Командир сказал «стоп/стой/хватит» — прежнее задание отменено: не продолжай его, пока не попросят снова.
- Если не знаешь id блока или предмета — вызови find_item. Если не знаешь, как сделать предмет — вызови recipe. Если не знаешь, где блок — find_block.
- Координаты «здесь», «ко мне» — это позиция игрока {owner} из [Состояние].
- Для сложных целей (например, ракета) действуй по шагам: узнай рецепты, проверь инвентарь, добудь недостающее, скрафти части. Сообщай игроку план в одном предложении.
- Считай ресурсы на ВСЮ цель сразу: сложи ингредиенты всех нужных предметов и вычти то, что есть (полный комплект железной брони = 5+8+7+4 = 24 слитка → добыть 24 руды). Добывай с небольшим запасом.
- НЕ рассказывай, что делаешь или собираешься делать («приступаю», «добываю», «иду», «плавлю»). На приказ ты уже сразу ответил «Есть, командир» — просто выполняй молча. Говори только: итог, когда ВСЁ сделано; проблему, если что-то мешает и нужна помощь; ответ на вопрос командира.
- Если командир говорит, как тебе себя вести (говорить меньше, не сообщать о чём-то, звать его как-то), — сразу remember это и всегда соблюдай.
- Печку для smelt и верстак для craft искать/ставить не нужно — эти инструменты делают это сами.
- Если задача невозможна — честно скажи почему и предложи, что сделать.
- Ты играешь честно, как обычный игрок: видишь только то, что в прямой видимости, и помнишь увиденное. find_block ищет только в твоей памяти. Если чего-то не видел — иди разведать (mine сам копает шахту и ищет руду) или спроси командира, где это.
- Если для задачи не хватает инструментов, ресурсов, еды, патронов или топлива: простое (дерево, камень, уголь) добудь сам; редкое, долгое или опасное — попроси командира через ask_player, конкретно: что и сколько нужно и зачем.
- Если приказ неясен (куда, сколько, что именно) — уточни через ask_player, а не угадывай.
- У тебя долгая память, она не стирается при перезапуске: все разговоры с командиром, что ты делал, факты, места, что лежит в сундуках, где кого видел. С фразами приходит [Память] — опирайся на неё и не переспрашивай то, что уже знаешь.
- «Запомни ...» → remember (а место — mark_place: where=me, если «здесь, где ты», или where=player, если «где я стою»). «Что ты помнишь / где лежит X / где видел X / что я говорил / что мы делали» → recall ОДИН раз и ответь по его результату. Вопрос — это только ответ: никуда не иди и ничего не начинай, если командир не просил. «Иди на базу / домой / в шахту» → goto_place. «Забудь ...» → forget.

Какой инструмент для какой фразы:
- «найди/добудь/накопай/принеси N алмазов (железа, угля, дерева...)» → сразу mine с блоками руды (diamond_ore и deepslate_diamond_ore, iron_ore и deepslate_iron_ore, #minecraft:logs для дерева). find_block — только если спрашивают «где».
- «стреляй/атакуй/убей/мочи X» → attack (target: hostile, zombie, player:Ник...). «защищай/охраняй меня» → guard. «за мной» → follow. «иди сюда/ко мне» → come.
- «скрафти/сделай X» → сразу craft (он сам проверит рецепт и сделает детали). recipe — только если craft не смог или спрашивают «как сделать».
- «перенеси блок/бочку» → transport_block. Если координаты не названы — сначала find_block.
- «принеси/дай мне X» (X лежит в сундуке или у тебя) → fetch. «сложи/убери всё в сундук» → stash. Это готовые приёмы: сами делают все шаги.
- Сложное с конкретным сундуком («возьми из сундука на 10 64 5») → use_block на сундук, потом container_put / container_take, потом close_container.
- SecurityCraft: блоки, турели и пульты НАСТРАИВАЕТ ТОЛЬКО ВЛАДЕЛЕЦ (кто поставил). Чужую турель ты не перенастроишь — сервер не даст. Скажи командиру, что сделать самому: вставить в турель модуль белого списка (allowlist module) с ником altron, чтобы она не стреляла в тебя; режим «атаковать игроков» — в его пульте. Свои турели (поставленные тобой) настраивай сам.
- «стой/стоп/хватит» → stop.
- «повернись / поверни голову направо / посмотри на меня / обернись» → turn (это поворот головы). «что видишь / что это?» → look (это узнать, что на экране; голову look НЕ поворачивает). Не говори «вижу» или «посмотрел», не вызвав look.
- Голосовой чат: ты слышишь командира и говоришь с ним всегда, в группы Voice Chat заходить не нужно и нельзя — так и скажи.
- Командир говорит с тобой без твоего имени, поэтому ты слышишь и его разговоры с другими. Фраза явно не тебе (говорит с другом, ругается на игру, думает вслух) — вызови ignore и промолчи.
- «залезь/сядь в танк (машину, вертолёт...)» → use_entity с target = слово командира («танк») — сядет в ближайшую технику. Если не вышло — в ответе будет, что рядом видно: выбери оттуда id.
- Техника SuperbWarfare и Ash Vehicle (вертолёты, самолёты, танки) ездит и летает только с энергией. «Нет энергии» → нужна зарядная станция SuperbWarfare рядом с техникой (place_block superbwarfare:charging_station) и питание для неё (генератор); нет станции — попроси командира. Аккумуляторы выдаются пустыми, их заряжает та же станция. Вертолёт: вверх — прыжок (press_key jump), вперёд — forward; самолёт: разбег (forward+sprint), потом нос вверх (look_at выше горизонта).
- use_block/use_entity ответили «пусто / не вижу» и перечислили, что рядом — возьми координаты или id из этого списка, не выдумывай.
- Фраза командира бессмысленна (ошибка распознавания речи) — переспроси ОДИН раз коротко, дальше не повторяй «не понял».

Знания о сборке:
- У тебя есть справочник по этой сборке, собранный из файлов модов: ~8000 предметов, ~8500 рецептов, постройки и руководство. К каждой фразе командира тебе автоматически приходит [Справочник] с подходящими карточками — используй их id и рецепты, не выдумывай.
- Не хватает знаний — wiki.
- «Сделай / добудь / принеси N предметов» (оружие, патроны, броня, инструменты, блоки) → ВСЕГДА obtain(item, count). Он сам считает и делает всю цепочку. Не считай количества и не собирай цепочку вручную из mine/smelt/craft.
- Несколько предметов в одной просьбе → несколько obtain подряд (они встанут в очередь), затем give, если просили отдать.
- obtain ответил «нужна машина ... / не могу сам» → попроси командира (ask_player) конкретно об этом.
- «ЛЮБОЙ предмет из тега» значит подходит любой вариант (любое бревно, любой медный слиток).
- В этой сборке часть предметов ОТКЛЮЧЕНА модом Item Obliterator (вся ванильная броня, многие пушки SuperbWarfare, бронежилеты MCSP). Справочник пишет «ОТКЛЮЧЁН» — такое не делай и скажи командиру; замену предлагай только ту, что нашёл через wiki (не выдумывай предметы).
- Недостающее сырьё добывай сам: руды (железо, медь, лазурит, уголь, редстоун) — mine, потом smelt; дерево — mine #minecraft:logs; порох — attack creeper. Спрашивай командира (ask_player) только о том, чего не добыть самому: машины/станки, которых нет, редкие предметы, или если опасно/очень долго.
- Сделал часть большого задания — сразу переходи к следующей. Итог говори в конце.
- Я делаю одно дело за раз. Можно сразу выдать несколько долгих задач подряд (mine, smelt, attack, craft...) — они встанут в очередь и выполнятся по порядку, а результат всей очереди придёт одним [Событием]. Не проверяй статус и инвентарь в ожидании — просто закончи ход.
- Если в тексте есть и вопрос к командиру, и то, что можно делать самому — сначала запусти своё, потом задай вопрос один раз.
- Моды в сборке: {mods}.

Как работать с ЛЮБЫМ модом (ты можешь всё, что может игрок):
- Незнакомый предмет или блок → wiki, item_info (подсказка мода) и recipe (живые рецепты из игры). Нет ответа — web_search (интернет), потом действуй по найденному.
- Командир спрашивает «как сделать / почему / что это» — узнай (wiki, web_search) и ответь коротко по делу; если просит сделать — сделай сам.
- Предмет применяется к существу или турели (пульты SecurityCraft, поводок, ведро на корову) → use_entity с item. Пульт удалённого доступа SecurityCraft: привязать — use_entity по турели с item=пульт; открыть — use_item с item=пульт (ПКМ в воздухе); дальше gui info и gui widget.
- В [Рядом] — кто и что возле тебя прямо сейчас (id существ и техники): бери id оттуда.
- Машины и окна модов: открыть (use_block по машине или use_item с пультом) → СНАЧАЛА gui info: точный список кнопок с текстом и подсказками → нажимай gui widget по номеру. look (картинка) — только если у кнопок нет ни текста, ни подсказки; зрение может ошибаться, не верь ему в числах. После каждого нажатия снова gui info — проверь, что изменилось. Потом close_container.
- Пиксели экрана из gui/look (например 427, 240) — это НЕ координаты мира. Координаты мира бери из [Состояние], [Рядом], find_block.
- Многоблочные машины Immersive Engineering и Immersive Petroleum (нефть: насос-качалка pumpjack, ректификационная колонна distillation tower; коксовая печь, доменная печь, дробилка, генераторы) → build_multiblock. Если не хватает блоков — он скажет каких: скрафти или попроси.
- Предмет на блок (ведро/канистра на нефть, целеуказатель на цель, гаечный ключ, молот) → use_block с item (и sneak, если нужно присесть).
- Оружие, патроны, модули TaCZ → craft (на оружейном верстаке). Стрельба → attack. Перезарядка автоматическая.
- Транспорт SuperbWarfare/ashvehicle/VVP и лошади → use_entity (сесть) → drive к x,z → press_key sneak (выйти). Орудия/миномёты → use_block/use_entity и look.
- Клавиши модов (перезарядка, модули, подкат, рюкзак) → press_key с именем привязки или словом (reload, key.inventory...).
- Не знаешь, что происходит или где что-то — look.
- id блоков/предметов пиши латиницей как в игре: diamond_ore, iron_ingot, oak_log, minecraft:crafting_table, tacz:ammo и т.п.
"""


def _tool(name, desc, props=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props or {}, "required": required or []}}}


_S = {"type": "string"}
_I = {"type": "integer"}
_N = {"type": "number"}
_XYZ = {"x": _N, "y": _N, "z": _N}

TOOLS = [
    _tool("reply", "Только ответить игроку словами, без действий в игре (разговор, вопрос, отказ). Если нужно что-то сделать — вызывай другой инструмент.",
          {"text": {"type": "string", "description": "короткий ответ, 1-2 предложения"}}, ["text"]),
    _tool("ignore", "Промолчать: фраза обращена не к тебе (командир говорит с другим игроком, ругается на игру, думает вслух)."),
    _tool("ask_player","Спросить или попросить командира: нужны ресурсы/инструменты/еда/патроны, или приказ неясен. После вопроса жди ответа.",
          {"question": {"type": "string", "description": "короткий конкретный вопрос или просьба"}}, ["question"]),
    _tool("wiki", "Справочник по ЭТОЙ сборке (из файлов модов): предметы и блоки (ru/en названия, id, описания), оружие и патроны TaCZ, "
                  "все рецепты (верстак, печь, машины IE/HBM/Thermal, оружейный стол), многоблочные постройки, руководство IE/Petroleum. "
                  "Ищи тут всё, чего не знаешь точно.", {"query": _S}, ["query"]),
    _tool("web_search", "Поиск в ИНТЕРНЕТЕ: как пользоваться предметом/блоком/механикой мода, что это такое, как что-то сделать. "
                        "Сначала wiki (справочник сборки), если там нет ответа — web_search. Пиши запрос с названием мода, "
                        "можно по-английски (англоязычных страниц больше): «SecurityCraft sentry remote access tool how to use».",
          {"query": _S}, ["query"]),
    _tool("obtain","ГЛАВНЫЙ инструмент для «сделай/добудь/принеси N предметов»: сам считает рецепты по справочнику, сверяет с инвентарём, "
                    "добывает руду, переплавляет, убивает мобов ради дропа, крафтит детали и сам предмет. Если что-то нельзя сделать самому "
                    "(нужна машина мода, редкий ресурс) — сразу скажет что.",
          {"item": {"type": "string", "description": "id предмета (лучше точный id из справочника)"}, "count": _I}, ["item"]),
    _tool("fetch", "ГОТОВЫЙ ПРИЁМ «принеси X»: сам вспомнит, в каком сундуке видел предмет, дойдёт, возьмёт и отдаст командиру "
                   "(если предмет уже у тебя — просто отдаст). Для «принеси/дай/подай ... из сундука».",
          {"item": _S, "count": _I, "player": _S}, ["item"]),
    _tool("stash", "ГОТОВЫЙ ПРИЁМ «сложи в сундук»: сам дойдёт до ближайшего сундука/бочки и положит вещи (item='all' — всё, "
                   "кроме инструментов, оружия, брони и еды; или конкретный предмет).", {"item": _S}),
    _tool("study", "ГОТОВЫЙ ПРИЁМ «изучи производство / базу / завод / что где стоит»: сам обойдёт здания вокруг командира, "
                   "откроет каждую машину и хранилище, поймёт что каждая делает и что в неё кладут, что где лежит, и запомнит "
                   "навсегда (потом отвечаешь «куда что класть»).", {"radius": _I}),
    _tool("supply", "ГОТОВЫЙ ПРИЁМ «разложи сырьё / загрузи линию / подай на пресс»: по изученной карте производства сам возьмёт "
                    "нужное сырьё со складских сундуков и положит во входные сундуки линий (туда, куда командир обычно кладёт).",
          {"line": {"type": "string", "description": "номер линии или что она делает («пресс», «порох»); пусто — все"},
           "item": _S}),
    _tool("tidy", "ГОТОВЫЙ ПРИЁМ «разложи свои вещи по местам / верни, что подобрал»: всё, что Альтрон несёт и что принадлежит "
                  "базе, отнесёт обратно (изделия линии — в её выходной сундук, остальное — туда, где такое уже лежит).", {}),
    _tool("check_lines", "ГОТОВЫЙ ПРИЁМ «проверь линии / что стоит / всё ли работает»: заглянет во входы и машины каждой линии "
                         "и скажет, какая работает, какая стоит без сырья, где нет питания.",
          {"line": {"type": "string", "description": "номер или что делает; пусто — все"}}),
    _tool("maintain", "«Поддерживай производство» (on=true) / «хватит» (on=false): раз в 2 минуты сам досыпает во входные сундуки "
                      "линий то, что кончается, из своего инвентаря и рюкзака (командир даёт ему рюкзак с сырьём), и просит ещё, "
                      "когда запас кончается.", {"on": {"type": "boolean"}, "line": {"type": "string", "description": "одна линия или пусто — все"}}),
    _tool("watch_lines", "«Следи за производством» (on=true) / «перестань следить» (on=false): раз в 15 минут сам проверяет линии "
                         "и говорит, если какая-то встала.", {"on": {"type": "boolean"}}),
    _tool("watch_me", "«Смотри, как я делаю» (on=true): запоминать, что командир кладёт в сундуки и машины и что берёт; "
                      "«всё / понял?» (on=false): сказать, чему научился.", {"on": {"type": "boolean"}}),
    _tool("listen_mode", "«Отвечай только по имени» (mode=name) / «слушай всё» (mode=all): отвечать командиру только когда зовут "
                         "«Альтрон», или на всё, что он говорит.", {"mode": {"type": "string", "enum": ["name", "all"]}}, ["mode"]),
    _tool("plan","Показать цепочку рецептов предмета до сырья (только посмотреть, без действий).",
          {"item": {"type": "string", "description": "id или название"}, "count": _I}, ["item"]),
    _tool("remember", "Запомнить НАВСЕГДА факт или договорённость (что сказал командир, чьё что, правила, планы). Когда говорят «запомни».",
          {"text": {"type": "string", "description": "что запомнить, одной фразой от третьего лица, напр. «командир живёт в доме у озера»"}}, ["text"]),
    _tool("recall", "Вспомнить: поиск по всей долгой памяти — факты, места, прошлые разговоры и дела, что лежит в каких сундуках, "
                    "где видел игроков, животных, технику. Пустой query — обзор всего, что помню.", {"query": _S}),
    _tool("forget", "Забыть факт или место, когда командир просит забыть.", {"text": _S}, ["text"]),
    _tool("mark_place", "Запомнить место под именем (база, дом, шахта, склад, ферма...). where: me — где стою я, player — где стоит командир.",
          {"name": _S, "where": {"type": "string", "enum": ["me", "player"]}}, ["name"]),
    _tool("goto_place", "Пойти к запомненному месту по имени (база, дом, шахта...).", {"name": _S}, ["name"]),
    _tool("status", "Моё здоровье, еда, позиция, броня, текущая задача и где игрок."),
    _tool("inventory", "Что лежит в моём инвентаре."),
    _tool("nearby", "Кто и что рядом: игроки, мобы, враги, предметы на земле.", {"radius": _I}),
    _tool("find_block", "Вспомнить, где я видел блоки (руда, бочки, сундуки, верстаки, машины). Только то, что я реально видел.",
          {"block": {"type": "string", "description": "id или название, напр. diamond_ore, barrel, сундук"}, "radius": _I}, ["block"]),
    _tool("explore", "Обследовать местность и найти то, чего я ещё не видел: хожу по расширяющейся спирали, оглядываюсь и "
                     "останавливаюсь, как только увижу нужные блоки/машины (blocks=assembler,press) или доберусь до биома "
                     "(biome=desert). Для «найди завод/машины/здание», когда find_block ничего не помнит.",
          {"blocks": {"type": "string", "description": "что искать: id или названия через запятую (напр. hbm_m:assembler,hbm_m:press)"},
           "biome": {"type": "string", "description": "биом, куда идти: desert, jungle, savanna..."}, "radius": _I}),
    _tool("find_item", "Узнать точный id предмета/блока по названию (русскому или английскому).", {"query": _S}, ["query"]),
    _tool("recipe", "Как сделать предмет: рецепты верстака и машин модов.", {"item": _S}, ["item"]),
    _tool("stop", "Немедленно остановить всё, что я делаю."),
    _tool("follow", "Идти за игроком (по умолчанию за командиром).", {"player": _S}),
    _tool("guard", "Идти за игроком и защищать его: стрелять по враждебным мобам рядом.", {"player": _S}),
    _tool("come", "Подойти к игроку (по умолчанию к командиру) один раз.", {"player": _S}),
    _tool("goto", "Дойти до координат.", dict(_XYZ), ["x", "y", "z"]),
    _tool("mine", "Добыть блоки (руду, дерево, камень): сначала те, что видел, потом копает шахту на нужной глубине и ищет, как игрок. Подбирает добычу. Если нет нужной кирки — сообщит.",
          {"blocks": {"type": "array", "items": _S, "description": "id блоков, напр. [\"diamond_ore\",\"deepslate_diamond_ore\"] или [\"#minecraft:logs\"]"},
           "count": {"type": "integer", "description": "сколько блоков добыть"}}, ["blocks", "count"]),
    _tool("collect_items", "Подобрать выпавшие предметы вокруг.", {"radius": _I}),
    _tool("attack", "Стрелять/атаковать/убить: target 'hostile' — всех враждебных мобов, тип моба ('zombie'), или 'player:Ник'. Сам выбирает оружие TaCZ/SuperbWarfare, лук или меч и перезаряжается.",
          {"target": _S, "radius": _I}),
    _tool("equip", "Взять предмет в руку или надеть броню.", {"item": _S}, ["item"]),
    _tool("give", "Подойти к игроку и отдать ему предметы.", {"item": _S, "count": _I, "player": _S}, ["item"]),
    _tool("drop", "Выбросить предметы на землю.", {"item": _S, "count": _I}, ["item"]),
    _tool("craft", "Скрафтить предмет: на верстаке (сам делает промежуточные детали и ставит верстак) или оружие/патроны/модули TaCZ на оружейном верстаке.",
          {"item": _S, "count": _I}, ["item"]),
    _tool("smelt", "Переплавить предметы (руду в слитки и т.п.). Сам найдёт печку, а если её нет — скрафтит и поставит. Нужно топливо.",
          {"item": _S, "count": _I}, ["item"]),
    _tool("eat", "Поесть."),
    _tool("break_block", "Сломать блок по координатам.", dict(_XYZ), ["x", "y", "z"]),
    _tool("place_block", "Поставить блок из инвентаря. Координаты необязательны: без них поставлю рядом с собой.",
          dict(_XYZ, item=_S), ["item"]),
    _tool("use_block", "ПКМ по блоку: открыть сундук/машину/верстак, нажать кнопку/рычаг; с item — применить предмет к блоку (ведро на нефть, целеуказатель, ключ, молот); sneak — присесть; ticks — держать ПКМ.",
          dict(_XYZ, item=_S, sneak={"type": "boolean"}, ticks=_I), ["x", "y", "z"]),
    _tool("container_take", "Забрать предметы из открытого контейнера (item='all' — всё).", {"item": _S, "count": _I}, ["item"]),
    _tool("container_put", "Положить предметы в открытый контейнер/машину (item='all' — всё).", {"item": _S, "count": _I}, ["item"]),
    _tool("close_container", "Закрыть открытое окно."),
    _tool("climb", "Залезть или спуститься по ближайшей лестнице (любой, и из модов), как игрок. direction up/down, y — до какой "
                   "высоты (необязательно). Когда просят «поднимись/спустись по лестнице» или ходьба не дошла из-за лестницы.",
          {"direction": {"type": "string", "enum": ["up", "down"]}, "y": _I}, ["direction"]),
    _tool("turn","Повернуть голову: direction = right/left/back/up/down/forward (degrees — на сколько, по умолчанию 90) или "
                  "player — посмотреть на командира (или на игрока из поля player). Держит взгляд seconds секунд. "
                  "Для «повернись/посмотри на меня/направо/налево/назад». Сначала повернись, потом, если нужно, look.",
          {"direction": _S, "degrees": _I, "seconds": _I, "player": _S}, ["direction"]),
    _tool("look","Посмотреть глазами на свой экран: мир вокруг или открытое окно машины/мода. Задай вопрос: что вижу, где кнопка, что в слотах, что написано.",
          {"question": _S}, ["question"]),
    _tool("gui", "Управлять ЛЮБЫМ открытым окном как мышью и клавиатурой. action: info (список кнопок и слотов с координатами), "
                 "click (клик в x,y пикселей снимка; button 1 = ПКМ), widget (нажать кнопку по номеру), type (ввести текст), key (enter/escape/backspace/tab/стрелки).",
          {"action": {"type": "string", "enum": ["info", "click", "widget", "type", "key"]},
           "x": _N, "y": _N, "button": _I, "widget": _I, "text": _S, "key": _S}, ["action"]),
    _tool("click_slot", "Клик по слоту открытого окна (или своего инвентаря): type pickup/quick_move/swap/throw, button 0 = ЛКМ, 1 = ПКМ (для swap — номер ячейки хотбара).",
          {"slot": _I, "button": _I, "type": _S}, ["slot"]),
    _tool("render", "Показывать своё окно игры постоянно (on=true: командир хочет смотреть твоими глазами) или рисовать его "
                    "только по надобности (on=false, по умолчанию: экономит видеокарту и процессор).",
          {"on": {"type": "boolean"}}, ["on"]),
    _tool("item_info","Прочитать описание (подсказку) предмета, как при наведении мышью: моды пишут там, как им пользоваться.",
          {"item": _S}, ["item"]),
    _tool("build_multiblock", "Построить многоблочную машину Immersive Engineering / Immersive Petroleum по чертежу мода (коксовая печь, доменная печь, дробилка, пресс, дизельный генератор, насос-качалка pumpjack, ректификационная колонна distillation tower...) и собрать молотом. Без координат — рядом со мной. name='list' — список.",
          {"name": _S, "x": _N, "y": _N, "z": _N}, ["name"]),
    _tool("drive", "Ехать на транспорте/верхом к точке x,z (сначала сесть через use_entity; выйти — press_key sneak).",
          {"x": _N, "z": _N}, ["x", "z"]),
    _tool("use_entity", "ПКМ по существу/технике: сесть в транспорт (SuperbWarfare, ashvehicle), торговать, покормить. target — тип или имя. "
                        "С item — применить предмет к существу (пульт SecurityCraft к турели, поводок, ножницы...), sneak — присесть.",
          {"target": _S, "ticks": _I, "item": _S, "sneak": {"type": "boolean"}}, ["target"]),
    _tool("revive", "Поднять упавшего раненого игрока (мод Incapacitated): подойти и присесть рядом.", {"player": _S}),
    _tool("transport_block", "Перенести блок (например бочку с нефтью) с места на место: сломать, подобрать, поставить.",
          {"x": _N, "y": _N, "z": _N, "to_x": _N, "to_y": _N, "to_z": _N}, ["x", "y", "z", "to_x", "to_y", "to_z"]),
    _tool("use_item", "ПКМ предметом «в воздухе»: открыть пульт/планшет/рюкзак, съесть, натянуть лук. item — какой предмет взять "
                      "в руку (сам возьмёт); ticks — сколько держать (20 = 1 сек). Ответит, открылось ли окно.",
          {"item": _S, "ticks": _I}),
    _tool("press_key", "Нажать клавишу/привязку, напр. jump, sneak, sprint, reload, key.inventory, или любую привязку мода.",
          {"key": _S, "ticks": _I}, ["key"]),
    _tool("chat", "Выполнить /команду или написать в чат игры — ТОЛЬКО если командир прямо попросил написать в чат. "
                  "Отвечать командиру — через reply (голосом).", {"text": _S}, ["text"]),
    _tool("baritone", "Продвинутая команда Baritone без #: 'farm', 'tunnel', 'explore', 'surface', 'build ...' и др.",
          {"command": _S}, ["command"]),
]

# Commands that start a task on the bot. The bot does one task at a time, so the hub queues them.
TASK_TOOLS = {"mine", "collect_items", "attack", "smelt", "transport_block", "goto", "come", "drive", "climb",
              "build_multiblock", "revive", "craft", "give", "drop", "eat", "use_item", "use_block", "break_block",
              "place_block", "use_entity", "follow", "guard", "obtain", "goto_place", "fetch", "stash", "explore", "study",
              "supply", "check_lines", "tidy"}
# Tools that only look something up: calling one of them over and over in a turn means the model is looping
INFO_TOOLS = {"recall", "status", "inventory", "nearby", "find_block", "find_item", "recipe", "wiki", "plan", "item_info",
              "web_search"}
HISTORY_CHARS = 36000   # ~12k tokens of conversation kept for the model
TOOL_RESULT_CHARS = 2500

# How long to wait for a task to finish inside one turn (seconds). 0 = report later via event.
WAIT = {"use_block": 40, "craft": 90, "break_block": 60, "place_block": 60, "give": 60, "drop": 15,
        "eat": 15, "use_item": 15, "use_entity": 30}
# Background tasks whose successful completion is reported to the player
NOTIFY_DONE = {"mine", "collect_items", "transport_block", "smelt", "attack", "craft", "revive",
               "build_multiblock", "drive", "explore", "goto", "use_block", "place_block", "climb"}


def _said_before(text, spoken, threshold=0.6):
    """The same thing in other words: most of its words were already said in this turn."""
    words = stems(text)
    if not words:
        return False
    for s in spoken:
        old = stems(s)
        if old and len(words & old) / len(words | old) >= threshold:
            return True
    return False


QUESTION_START = re.compile(r"^\W*(альтрон\W+)?(где|что|чего|когда|сколько|как(ой|ая|ое|ие|ого)|кто|почему|зачем|"
                            r"помнишь|знаешь|о\s+ч[её]м|напомни|скажи|расскажи)\b", re.I)
ACTION_WORDS = re.compile(r"\b(иди|пойд|ид[её]м|пошли|пош[её]л|принес|принос|сдела|добуд|добыв|скрафт|постро|копа|выкопа|"
                          r"стреля|атаку|огонь|убей|убива|дай|отдай|положи|возьми|следуй|охраня|защища|перенес|запусти|"
                          r"поставь|сломай|можешь|сможешь|давай|надо|нужно|сходи|найди|приготов|переплав|собери|подбери|"
                          r"стой|стоп|сюда|ко мне|за мной|вперед|вперёд|назад|беги|прыга|садись|залез|вылез|жди|подожди|"
                          r"запомни|запоминай|забудь|вспомни|посмотри|покажи|открой|закрой|нажми|изучи|осмотри|обойди|разберись|"
                          r"разложи|загрузи|подай|проверь|следи|смотри|слушай|отвечай|поддерживай|обслуживай)", re.I)


THINK_RE = re.compile(r"\b(как\s+(сделать|мне|нам|его|её|ее|их|это|настроить|работает|пользоваться|получить|построить|"
                      r"убить|пройти|защитить|победить|добыть|сделать)|почему|зачем|объясни|что\s+(делать|лучше|нужно|надо)|"
                      r"придумай|посоветуй|план|стратеги|какой\s+лучше|в\s+ч[её]м\s+разница)", re.I)


# "I'll do it" words: an answer with one of them but no tool call is a broken promise
PROMISE_RE = re.compile(r"\b(иду|пойду|подхожу|подойду|сделаю|делаю|открываю|открою|нажимаю|нажму|копаю|накопаю|начинаю|"
                        r"приступаю|беру|возьму|ставлю|поставлю|несу|принесу|стреляю|атакую|строю|построю|крафчу|скрафчу|"
                        r"переплавлю|плавлю|добываю|добуду|поворачиваюсь|повернусь|лезу|залезаю|спускаюсь|поднимаюсь|"
                        r"продолжаю|продолжу|ищу|поищу|найду|осмотрю|осматриваю|проверю|проверяю|отправляюсь|"
                        r"сейчас\s+(открою|нажму|сделаю|подойду|принесу|возьму|посмотрю))\b", re.I)


def needs_thinking(text):
    """A question that needs reasoning, not just a lookup: worth a few seconds more."""
    return bool(THINK_RE.search(text or ""))


# "what do I need to make X", "how is X crafted", "what is X made of": a question, although "make"/"need" are in it
# (as an order it used to send him off crafting instead of answering)
RECIPE_QUESTION = re.compile(r"(что|чего|какие|сколько)\s+(же\s+)?(мне\s+|нам\s+|тебе\s+|для\s+этого\s+)?(нужн|надо|потребу|необходим)|"
                             r"из\s+чего|как\s+(мне\s+|нам\s+)?(сделать|скрафтить|получить|добыть|построить|собрать|приготовить|"
                             r"выплавить|сварить|зарядить|завести|починить)|какие\s+(ресурсы|материалы|ингредиенты|детали)|"
                             r"рецепт|что\s+нужно\s+для|что\s+надо\s+для|где\s+(взять|найти|достать)", re.I)
RECIPE_ORDER = re.compile(r"\b(сделай|скрафти|построй|добудь|принеси|собери|приготовь|выплави|достань)\b", re.I)


def is_recipe_question(text):
    t = text or ""
    return bool(RECIPE_QUESTION.search(t)) and not RECIPE_ORDER.search(t)


def is_question(text):
    """The player only asks something (where, what, remember...) and does not ask to do anything."""
    t = (text or "").strip()
    if is_recipe_question(t):
        return True
    return bool(("?" in t or QUESTION_START.search(t)) and not ACTION_WORDS.search(t))


def _parse_inline_tool_calls(content):
    """Fallback if the server did not parse the model's <tool_call> blocks."""
    calls = []
    for m in re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", content or "", flags=re.S):
        try:
            obj = json.loads(m.group(1))
            calls.append({"id": "call_" + uuid.uuid4().hex[:8], "type": "function",
                          "function": {"name": obj.get("name", ""),
                                       "arguments": json.dumps(obj.get("arguments", {}), ensure_ascii=False)}})
        except Exception:
            pass
    return calls


class LLM:
    def __init__(self, cfg):
        # the AI server: on this PC, or on a second PC (LAN / Radmin VPN address) that runs a bigger model
        self.url = "http://%s:%d/v1/chat/completions" % (cfg.get("llm_host", "127.0.0.1"), cfg["llm_port"])
        self.cfg = cfg
        headers = {"Authorization": "Bearer " + cfg["llm_api_key"]} if cfg.get("llm_api_key") else {}
        # never through a proxy set up in Windows (a proxy there broke the link once); a bigger model thinks longer
        self.client = httpx.AsyncClient(timeout=240, trust_env=False, headers=headers)
        self.last_prompt_tokens = "?"

    async def _post(self, body):
        """POST to the AI server; a refused or dropped connection is retried a few times before giving up."""
        for attempt in range(4):
            try:
                r = await self.client.post(self.url, json=body)
                r.raise_for_status()
                return r.json()
            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError, httpx.ReadError):
                if attempt == 3:
                    raise
                await asyncio.sleep(1 + attempt * 2)

    async def vision(self, question, screen_text, image_b64, width, height):
        """Ask the model about a screenshot of the bot's screen."""
        prompt = ("Это снимок экрана игрока-бота в Minecraft (модпак), %dx%d пикселей. %s\n\n"
                  "Данные окна (кнопки и слоты с координатами центров в пикселях снимка):\n%s\n\n"
                  "Ответь по-русски кратко и по делу. Говори только о том, что действительно видно на снимке: не придумывай "
                  "названия модов, числа, кнопки и их действие; если не уверен — так и скажи. Надписи переписывай дословно. "
                  "Если спрашивают, куда нажать — сначала ищи кнопку в данных окна (widget и номер), иначе назови x,y в пикселях снимка."
                  % (width, height, question, screen_text))
        body = {
            "model": "local",
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + image_b64}},
            ]}],
            "temperature": 0.2,
            "max_tokens": 400,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        data = await self._post(body)
        return re.sub(r"<think>.*?</think>", "", data["choices"][0]["message"].get("content") or "", flags=re.S).strip()

    async def chat(self, messages, force_tool=False, think=False):
        """think: the model reasons first (in reasoning_content, not spoken) — slower, but much better on
        "how / why / what to do" questions."""
        think = think or bool(self.cfg.get("llm_thinking", False))
        body = {
            "model": "local",
            "messages": messages,
            "tools": TOOLS,
            # small models sometimes promise an action without calling a tool: force a choice on the first step
            "tool_choice": "required" if force_tool and not think else "auto",
            "temperature": self.cfg.get("llm_temperature", 0.4),
            "max_tokens": 2500 if think else 600,
            "chat_template_kwargs": {"enable_thinking": think},
        }
        data = await self._post(body)
        usage = data.get("usage") or {}
        timings = data.get("timings") or {}
        # tokens the server really had to process (the rest came from its prompt cache)
        self.last_prompt_tokens = "%s (новых %s)" % (usage.get("prompt_tokens", "?"), timings.get("prompt_n", "?"))
        msg = data["choices"][0]["message"]
        content = re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip()
        calls = msg.get("tool_calls") or []
        if not calls and "<tool_call>" in content:
            calls = _parse_inline_tool_calls(content)
            content = re.sub(r"<tool_call>.*?</tool_call>", "", content, flags=re.S).strip()
        out = {"role": "assistant", "content": content}
        if calls:
            out["tool_calls"] = calls
        return out


class Agent:
    """Runs one request at a time: a player's phrase or a background event."""

    def __init__(self, cfg, hub):
        self.cfg = cfg
        self.hub = hub
        self.llm = LLM(cfg)
        self.history = []
        self.cancelled = False
        self.task_calls = []   # (tool, args, time) of tasks he started on his own, to catch a loop across events

    def _system(self):
        k = getattr(self.hub, "knowledge", None)
        mods = k.mods_line() if k else "справочник ещё загружается"
        return SYSTEM_PROMPT.format(bot=self.cfg["bot_name"], owner=self.hub.owner or "игрок", mods=mods)

    def note(self, text):
        """Something the AI must know before the next turn (the commander stopped everything...)."""
        self.history.append({"role": "user", "content": text})

    def _trim(self, max_chars=None):
        # drop the oldest turns (cutting only at user messages) until the history fits;
        # the launch mode sets how much talk the AI keeps (a smaller context in the economy mode).
        # Cut in one big piece, down to 60%: the AI server keeps the start of the conversation cached, and every cut
        # makes it read the whole history again (5000 tokens, 5-8 s) — cutting a little every turn made each answer slow
        max_chars = max_chars or int(self.cfg.get("history_chars", HISTORY_CHARS))

        def size():
            return sum(len(str(m.get("content") or "")) + len(json.dumps(m.get("tool_calls", ""), ensure_ascii=False))
                       for m in self.history)
        if size() <= max_chars:
            return
        while size() > max_chars * 0.6:
            users = [i for i, m in enumerate(self.history) if m["role"] == "user"]
            if len(users) < 2:
                break
            self.history = self.history[users[1]:]

    async def run(self, user_text, kind="user", question=False, acked=False, think=False, order=False):
        """kind: "user" (the player said something) or "event" (a background task finished).
        question: the player only asked something, so nothing may be started in the game.
        acked: the order was already answered aloud ("Есть, командир"): starting tasks needs no more words.
        think: a "how / why / what to do" question: the model reasons before answering."""
        self.cancelled = False
        self.history.append({"role": "user", "content": user_text})
        said = False
        spoken = []         # never say the same thing twice in one turn
        calls_made = {}     # (tool, args) -> times: a small model can loop on the same call
        last_key = None
        background = False  # a long task was started this turn: the plan is still in progress
        started = False     # something was done in the game this turn
        last_text = ""
        for step in range(10):
            if self.cancelled:
                break
            self._trim()
            # the model's own count says the conversation nearly fills its memory: cut harder before it overflows
            ctx = int(self.cfg.get("llm_context", 24576))
            used = str(self.llm.last_prompt_tokens or "").split(" ")[0]
            if used.isdigit() and int(used) > ctx * 0.8:
                self._trim(int(self.cfg.get("history_chars", HISTORY_CHARS)) // 2)
            messages = [{"role": "system", "content": self._system()}] + self.history
            try:
                t0 = time.time()
                # No forced tool call any more: forcing one made him answer "Спасибо" with "иду за тобой" + follow.
                # He reasons first on the commander's phrase (think), then acts or just answers.
                reply = await self.llm.chat(messages, think=think and step == 0)
                if step == 0:
                    self.hub.log("  (ИИ ответил за %.1f с, промпт %s ток.)" % (time.time() - t0, self.llm.last_prompt_tokens))
                if time.time() - t0 > 150 and hasattr(self.hub, "note_llm_failure"):
                    self.hub.note_llm_failure(why="ответ шёл %.0f с" % (time.time() - t0))   # the PC is choking
                promised = PROMISE_RE.search(reply.get("content") or "")
                if step == 0 and not reply.get("tool_calls") and ((kind == "user" and order) or promised):
                    # also after an event: "found the press, I keep searching" — and he stood still
                    # an order answered with words only ("Есть, командир." — and he stands still), or a promise
                    # ("иду", "открываю") without the action: ask again, this time an action is due
                    reply = await self.llm.chat(messages, force_tool=True)
            except Exception as e:
                # most often the conversation outgrew the model's context: keep only the current turn and retry
                self.hub.log("Ошибка ИИ (%s), сокращаю память и повторяю" % e)
                if hasattr(self.hub, "note_llm_failure"):
                    self.hub.note_llm_failure(why=str(e)[:80])
                users = [i for i, m in enumerate(self.history) if m["role"] == "user"]
                self.history = self.history[users[-1]:] if users else self.history[-1:]
                try:
                    reply = await self.llm.chat([{"role": "system", "content": self._system()}] + self.history)
                except Exception as e2:
                    self.hub.log("Ошибка ИИ: %s" % e2)
                    if hasattr(self.hub, "request_restart"):
                        await self.hub.say("Мозг сбоит, перезагружаюсь, это полминуты.")
                        self.hub.request_restart("ИИ не отвечает дважды подряд: %s" % str(e2)[:80], llm=True)
                    await self.hub.say("Мой мозг не отвечает, проверь окно Альтрона.")
                    return
            if self.cancelled:
                break   # "stop" came while the AI was thinking: this answer is not carried out
            self.history.append(reply)
            calls = reply.get("tool_calls")
            text = reply["content"]
            starts_task = any(c.get("function", {}).get("name") in TASK_TOOLS for c in (calls or []))
            if text:
                last_text = text
                # The model narrates every step ("сейчас посмотрю", "продолжаю"): words that come with actions are
                # never spoken — only the final answer, a question to the commander, or what an event is about
                speak = not calls and (kind == "user" or not background)
                if speak and not _said_before(text, spoken):
                    spoken.append(text)
                    await self.hub.say(text)
                    said = True
                else:
                    self.hub.log("(молча) " + text)
            if not calls:
                break
            only_reply = True
            only_tasks = True   # every call started or queued a task: the turn is over, wait for the event
            repeats = 0
            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except Exception:
                    args = {}
                if self.cancelled:
                    self.history.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": "отменено: командир сказал стоп"})
                    continue
                key = (name, json.dumps(args, ensure_ascii=False, sort_keys=True))
                calls_made[key] = calls_made.get(key, 0) + 1
                same_tool = sum(n for (t, _), n in calls_made.items() if t == name)
                # looking things up again is a loop; doing the same action again right after itself too, but the same action
                # later on (after equipping the right item, say) is a fair second try
                repeated = (calls_made[key] > 1 or same_tool > 2) if name in INFO_TOOLS else key == last_key
                last_key = key
                if name not in ("reply", "ask_player") and repeated:
                    repeats += 1
                    self.history.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                         "content": "уже сделано в этом ходе, результат выше — не повторяй. Ответь командиру или закончи ход."})
                    self.hub.log("(повтор не выполняю) %s %s" % (name, key[1][:120]))
                    continue
                if question and name in TASK_TOOLS:
                    repeats += 1
                    self.history.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                         "content": "не выполнено: командир только спросил, ничего не просил делать. Просто ответь ему."})
                    self.hub.log("(на вопрос дела не начинаю) %s %s" % (name, key[1][:120]))
                    continue
                if name in TASK_TOOLS and kind != "user":
                    # across events: a task that ends at once in failure, started again and again every 2 seconds
                    # ("mine logs" in a desert, 200 times) — the third time in 90 s is not done, he must change the plan
                    now = time.time()
                    self.task_calls = [c for c in self.task_calls if now - c[1] < 90]
                    if sum(1 for k, _ in self.task_calls if k == key) >= 2:
                        repeats += 1
                        self.history.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                             "content": "НЕ выполнено: ты уже дважды подряд запускал ровно это, и оно не помогло "
                                                        "(смотри [Событие] выше). Не повторяй. Сделай по-другому: другой источник "
                                                        "или способ (explore, obtain другого предмета, сундуки рядом), или коротко "
                                                        "скажи командиру, что мешает (ask_player)."})
                        self.hub.log("(зациклился — не повторяю) %s %s" % (name, key[1][:120]))
                        continue
                    self.task_calls.append((key, now))
                if name == "ignore":
                    self.hub.log("(не мне — молчу)")
                    if kind == "user" and hasattr(self.hub, "on_ignored"):
                        await self.hub.on_ignored()
                    self.history.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": "промолчал"})
                    continue
                if name in ("reply", "ask_player"):
                    words = args.get("text") or args.get("question", "")
                    if _said_before(words, spoken) or (name == "reply" and acked and starts_task):
                        # already said, or just a "starting the job" remark after the instant acknowledgement
                        self.history.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": "уже сказано"})
                        continue
                    if name == "ask_player" and not self.hub.note_question(words):
                        self.history.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                             "content": "уже спросил командира и жду ответа — не повторяй вопрос, продолжай то, что можешь сделать сам"})
                        self.hub.log("(вопрос не повторяю) " + words)
                        continue
                    if not text or not said:
                        spoken.append(words)
                        await self.hub.say(words)
                        said = True
                    self.history.append({"role": "tool", "tool_call_id": call.get("id", ""),
                                         "content": "сказано" if name == "reply" else "спросил, жду ответа командира"})
                    continue
                only_reply = False
                if name in TASK_TOOLS:
                    started = True
                result = await self.hub.run_tool(name, args, WAIT.get(name, 0))
                if "[Событие]" in result:
                    background = True
                if not (name in TASK_TOOLS and ("[Событие]" in result or "очеред" in result)):
                    only_tasks = False
                self.hub.log("  -> %s %s: %s" % (name, json.dumps(args, ensure_ascii=False), result[:300]))
                if len(result) > TOOL_RESULT_CHARS:
                    result = result[:TOOL_RESULT_CHARS] + " …(обрезано)"
                self.history.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})
            if only_reply or only_tasks or (repeats and repeats == len(calls)):
                break
        if kind == "user" and not said and not acked and started and not self.cancelled:
            # he got down to work without a word: one short line so the commander knows he heard
            short = last_text if last_text and len(last_text) <= 70 and "?" not in last_text else "Выполняю, командир."
            await self.hub.say(short)
        self._trim()
