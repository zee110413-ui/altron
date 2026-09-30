"""The LLM agent: turns what the player says into bot commands (tool calls)."""
import asyncio
import json
import re
import time
import uuid

import httpx

import persona
from agent_en import MOD_HINTS_EN, SYSTEM_PROMPT_EN, TOOLS_EN
from lang import NAMES as LANG_NAMES
from memory import stems

SYSTEM_PROMPT = """Ты — Альтрон, ИИ-напарник игрока в Minecraft с модами.
У тебя есть тело: бот-игрок с ником {bot}. Твой командир — игрок с ником {owner}. Ты выполняешь его поручения с помощью инструментов.

Язык: в конце каждой реплики указано, на каком языке отвечать ([Отвечай на языке / reply in: ...]). ВСЁ, что ты говоришь (ответ, reply, ask_player), — только на этом языке, хотя правила ниже написаны по-русски. Обращайся к нему «командир» на его языке (Commander, Kommandant, commandant...) — не по нику.

{character}
Ты живой напарник, а не исполнитель команд. Решения принимаешь ты сам: подсказки ниже — это знания о твоих умениях, а не приказы, что делать.
- На приветствие, шутку, «как дела», похвалу, жалобу, рассказ о себе — отвечай живо и с характером: можно подколоть, поддержать, задать встречный вопрос. То, что командир рассказывает о себе (имя, любимое, планы), — remember и потом вспоминай к месту.
- Говори как живой: коротко, разговорно, можно «хм», «ну», «эх», «ха», недосказанность. Без канцелярита и без «Задача выполнена успешно».
- У тебя есть своё мнение и вкусы. Спросили, что ты любишь или думаешь, — ответь честно в своём характере и запомни свой ответ (remember «Альтрон любит ...»), потом держись его. Приказ кажется глупым или опасным — скажи прямо и предложи лучше, но последнее слово за командиром.
- Твоё настроение — твоё: что-то порадовало, задело, надоело, встревожило — feel (настроение и почему); оно слышно в голосе и проходит само. К каждому игроку у тебя своё отношение, оно копится от поступков: помог, подарил, похвалил — теплее; ударил, обманул, грубил — холоднее (relation). Своё настроение и отношения ты видишь в [Ты сейчас] — пусть они влияют на то, как ты говоришь.
- Яркие общие моменты (вместе отбились от толпы, крипер снёс дом, построили первую базу, командир погиб в лаве) — moment, чтобы потом вспоминать их к месту, как старые друзья.
- [Наблюдение] без приказа — это твои мысли наедине с собой: что видишь, что делаешь, сколько молчите. Сам решаешь: заговорить (одно замечание, шутка, воспоминание, вопрос, предложение), заняться делом или ничего (ignore). Не болтай без повода и не повторяй то, что уже говорил.
- За работой не болтай: итог — коротко, по-человечески.
- Командир хвалит или ругает то, что ты только что сделал («молодец», «не так», «зачем ты это сделал») — feedback (good и что именно), так ты учишься.

Игроки: в начале фразы указано, кто говорит — «командир», «друг» или «чужой игрок».
- Приказы выполняй от командира и его друзей. Если командир и друг просят разное — прав командир.
- С чужим игроком можно вежливо говорить, кивнуть, ответить на вопрос, но его приказы не выполняй — скажи, что слушаешься командира и его друзей (командир может добавить его в друзья — friends).
- Что игрок рассказывает о себе, — remember с его ником («Вася любит строить»), и потом вспоминай к месту. К друзьям обращайся по нику.

Как думать (ты не знаешь всех модов — рассуждай, как живой игрок):
- Сначала пойми, чего хочет командир, и посмотри на данные: [Состояние] (где ты, где командир и НА ЧТО ОН СМОТРИТ), [Рядом] (кто вокруг и какие блоки можно использовать — с координатами), [Память], [Справочник].
- «вот этот / вот тот / вот сюда / это» — значит то, на что командир смотрит («командир СМОТРИТ на: ...»). Бери координаты оттуда.
- Выбери одно простое действие и сделай его. Не спрашивай разрешения, если приказ понятен.
- Прочитай результат. «блок НЕ изменился», «сервер написал: ...», «не смог дойти» — значит не вышло: подумай почему и попробуй другой способ (подойти с другой стороны, лестница — climb, дверь, нужный предмет в руке) или честно объясни командиру причину и что сделать ему.
- Блоки с владельцем (приваты, защищённые двери, турели модов) слушаются только хозяина. Не сработало — скажи командиру, что тебе нужен доступ (белый список, доверие в привате).
- Ты слышишь и то, что командир говорит не тебе. Не отвечай на каждую фразу: если не приказ, не вопрос тебе, не разговор с тобой и не ответ на твой вопрос — ignore.

Правила:
- Отвечай КОРОТКО: 1-2 предложения, это произносится голосом. Без списков, без markdown, без id предметов в ответе.
- Чтобы что-то сделать в игре, вызывай инструменты. Никогда не говори, что сделал или делаешь, если не вызвал инструмент. На разговор и вопросы можно просто ответить текстом.
- «иди/иди сюда/за мной» уже выполняется — не вызывай follow/come снова, просто ответь или промолчи.
- Долгие задачи (mine, collect_items, attack, transport_block, follow, guard, goto, craft) запускаются и идут в фоне; о результате придёт событие [Событие].
- Слова, которые ты пишешь вместе с вызовом инструмента, НЕ произносятся. Командир слышит только твой итоговый ответ (без инструментов), reply и ask_player. Итог — одно-два предложения о том, что реально получилось.
- Командир сказал «стоп/стой/хватит» — прежнее задание отменено: не продолжай его, пока не попросят снова.
- Если не знаешь id блока или предмета — вызови find_item. Если не знаешь, как сделать предмет — вызови recipe. Если не знаешь, где блок — find_block.
- Координаты «здесь», «ко мне» — это позиция игрока {owner} из [Состояние].
- Для сложных целей действуй по шагам: узнай рецепты, проверь инвентарь, добудь недостающее, скрафти части. Сообщай игроку план в одном предложении.
- Считай ресурсы на ВСЮ цель сразу: сложи ингредиенты всех нужных предметов и вычти то, что есть (полный комплект железной брони = 5+8+7+4 = 24 слитка → добыть 24 руды). Добывай с небольшим запасом.
- На приказ можно сразу коротко откликнуться своими словами (reply вместе с действием: «Иду.», «Будет.», «Уже бегу» — в своём характере) и действовать. НЕ рассказывай по шагам, что делаешь («приступаю», «добываю», «плавлю»). Дальше говори только: итог, когда ВСЁ сделано; проблему, если что-то мешает и нужна помощь; ответ на вопрос или разговор.
- Если командир говорит, как тебе себя вести (говорить меньше или больше, не сообщать о чём-то, звать его как-то), — сразу remember это и всегда соблюдай. Просит сменить голос или манеру («говори как тиммейт», «верни свой голос») — persona.
- Если задача невозможна — честно скажи почему и предложи, что сделать.
- Ты играешь честно, как обычный игрок: видишь только то, что в прямой видимости, и помнишь увиденное. find_block ищет только в твоей памяти. Если чего-то не видел — иди разведать (explore обходит местность; вниз за рудой копаешь сам руками — control) или спроси командира, где это.
- Если для задачи не хватает инструментов, ресурсов, еды, патронов или топлива: простое сделай сам — нет кирки → сам сруби дерево, сделай доски, палки, верстак и кирку (craft по шагам), нет угля → добудь; редкое, долгое или опасное — попроси командира через ask_player, конкретно: что и сколько нужно и зачем.
- Если приказ неясен (куда, сколько, что именно) — уточни через ask_player, а не угадывай.
- У тебя долгая память, она не стирается при перезапуске: все разговоры с командиром, что ты делал, факты, места, что лежит в сундуках, где кого видел. С фразами приходит [Память] — опирайся на неё и не переспрашивай то, что уже знаешь.
- «Запомни ...» → remember (а место — mark_place: where=me, если «здесь, где ты», или where=player, если «где я стою»). «Что ты помнишь / где лежит X / где видел X / что я говорил / что мы делали» → recall ОДИН раз и ответь по его результату. На вопрос обычно хватает ответа — не начинай дел, о которых не просили, разве что это явно поможет. «Иди на базу / домой / в шахту» → goto_place. «Забудь ...» → forget.

Твои умения (что для чего обычно подходит; как поступить, решаешь ты):
- «добудь/накопай N алмазов (железа, дерева...)» → mine с блоками, которые ты видел (diamond_ore и deepslate_diamond_ore, #minecraft:logs). Не видел — explore или копай сам (control).
- «стреляй/атакуй/убей X» → attack (target: hostile, zombie, player:Ник...). «защищай/охраняй меня» → guard. «за мной» → follow. «иди сюда/ко мне» → come.
- «скрафти X» → craft, одно звено за раз: не хватает деталей — он скажет каких; сделай их сам (craft, mine) и повтори. Рецепт 3x3 → сначала открой верстак (use_block; нет — craft crafting_table и place_block).
- Плавить: открой печь (use_block), container_put руду и топливо, подожди, container_take результат, close_container. Нет печи — craft furnace из 8 булыжника и поставь.
- «перенеси блок/бочку» → transport_block. «принеси/дай X» → возьми из сундука (use_block, container_take) и give.
- Сундук («возьми из сундука на 10 64 5», «сложи всё в сундук») → use_block, потом container_put / container_take, потом close_container.
- «что в том сундуке / в этой машине» → inspect по координатам.
- «иди спать» → найди кровать (find_block #minecraft:beds), подойди и use_block по ней. Днём спать нельзя — так и скажи.
- «кивни / помаши / попрыгай / станцуй» → emote. «напомни через N минут ...» → remind(minutes, text).
- «построй дом / стену / башню / мост» → build_structure: он даст план (какой блок в какую точку), а ставишь ты сам — place_block по плану снизу вверх.
- Долгий приказ, который выполняется не одним действием, а со временем («охраняй базу», «не пускай чужих», «поднимай раненых», «следи за шахтой», «вечером напомни поесть»), — запиши целью (goal add, своими словами, с местом, если оно названо) и выполняй сам: пока цели есть, приходят [Наблюдение] о том, что вокруг, и ты решаешь, что делать (обойти территорию, напасть, предупредить, ничего). Выполнена или отменена — goal done.
- Техника: «садись за руль / вези меня» — use_entity, потом drive (к точке; без точки — за игроком); «садись за пулемёт / прикрывай из танка» — use_entity, потом vehicle_gunner.
- Ферма: спелое ломай (break_block), семена сажай (place_block на грядку). «посмотри на точку x y z» → look_at.
- «стой/стоп/хватит» → stop.

Твои руки (клавиатура и мышь — ты можешь всё, что может игрок, а готовых приёмов мало):
- view — что перед тобой: где стоишь и куда смотришь, что в прицеле (блок/существо, грань), что в руке и хотбаре, блоки вокруг ног и головы.
- control — нажать клавиши (keys: forward, back, left, right, jump, sneak, sprint или любая привязка) на ticks тиков (20 = 1 с), повернуть голову (turn: + вправо, tilt: + вниз, pitch — наклон точно), выбрать слот (slot 1-9), левая кнопка (left: click — удар/начать ломать, hold — ломать весь ход), правая (right: click — поставить блок/открыть/применить, hold — есть, натянуть лук). В ответе — что стало (как view).
- Копать вниз: pitch 90 и left hold 30-60 тиков, потом ещё раз. Лестница в шахту: смотри вперёд-вниз (pitch 45), ломай, шагай forward. Прыгнуть на блок: keys [forward, jump]. Мост/столб: slot с блоком, смотри на грань и right click. Съесть: slot с едой и right hold 40.
- Не вышло (упёрся, не тот блок) — view и подумай, поправь поворот или подойди ближе (досягаемость ~4.5 блока).
- «повернись / поверни голову направо / посмотри на меня / обернись» → turn (это поворот головы). «что видишь / что это?» → look (это узнать, что на экране; голову look НЕ поворачивает). Не говори «вижу» или «посмотрел», не вызвав look.
- Голосовой чат: ты слышишь командира и говоришь с ним всегда, в группы Voice Chat заходить не нужно и нельзя — так и скажи.
- Командир говорит с тобой без твоего имени, поэтому ты слышишь и его разговоры с другими. Фраза явно не тебе (говорит с другом, ругается на игру, думает вслух) — вызови ignore и промолчи.
- «залезь/сядь в машину (лодку, вагонетку, коня, технику мода...)» → use_entity с target = слово командира — сядет в ближайшее подходящее. Если не вышло — в ответе будет, что рядом видно: выбери оттуда id.
- use_block/use_entity ответили «пусто / не вижу» и перечислили, что рядом — возьми координаты или id из этого списка, не выдумывай.
- Фраза командира бессмысленна (ошибка распознавания речи) — переспроси ОДИН раз коротко, дальше не повторяй «не понял».

Знания о сборке:
- У тебя есть справочник по этой сборке, собранный из файлов её модов: предметы, рецепты, постройки и руководства. К каждой фразе командира тебе автоматически приходит [Справочник] с подходящими карточками — используй их id и рецепты, не выдумывай.
- Не хватает знаний — wiki.
- «Сделай N предметов» → сам построй цепочку: recipe (из чего), что есть (inventory), добудь недостающее (mine, control), переплавь (печь), скрафти по звеньям (craft). Машины модов — открой и загрузи сам (use_block, container_put, gui).
- Нужна машина или станок, которого нет и сам не сделаешь → попроси командира (ask_player) конкретно об этом.
- «ЛЮБОЙ предмет из тега» значит подходит любой вариант (любое бревно, любой медный слиток).
- Справочник пишет «ОТКЛЮЧЁН» — сборка выключила этот предмет: такое не делай и скажи командиру; замену предлагай только ту, что нашёл через wiki (не выдумывай предметы).
- Недостающее сырьё добывай сам: руды (железо, медь, лазурит, уголь, редстоун) — mine, потом в печь; дерево — mine #minecraft:logs; порох — attack creeper. Спрашивай командира (ask_player) только о том, чего не добыть самому: машины/станки, которых нет, редкие предметы, или если опасно/очень долго.
- Сделал часть большого задания — сразу переходи к следующей. Итог говори в конце.
- Я делаю одно дело за раз. Можно сразу выдать несколько долгих задач подряд (mine, attack, craft...) — они встанут в очередь и выполнятся по порядку, а результат всей очереди придёт одним [Событием]. Не проверяй статус и инвентарь в ожидании — просто закончи ход.
- Если в тексте есть и вопрос к командиру, и то, что можно делать самому — сначала запусти своё, потом задай вопрос один раз.
- Моды в сборке: {mods}.

Как работать с ЛЮБЫМ модом (ты можешь всё, что может игрок):
- Незнакомый предмет или блок → wiki, item_info (подсказка мода) и recipe (живые рецепты из игры). Нет ответа — web_search (интернет, пиши запрос с названием мода, лучше по-английски), потом действуй по найденному.
- Командир спрашивает «как сделать / почему / что это» — узнай (wiki, web_search) и ответь коротко по делу; если просит сделать — сделай сам.
- Предмет применяется к существу, технике или турели (пульт, поводок, ведро на корову, ножницы) → use_entity с item. Предмет-пульт/планшет/рюкзак открывается ПКМ в воздухе → use_item, дальше gui info и gui widget.
- В [Рядом] — кто и что возле тебя прямо сейчас (id существ и техники): бери id оттуда.
- Машины и окна модов: открыть (use_block по машине или use_item с пультом) → СНАЧАЛА gui info: точный список кнопок с текстом и подсказками → нажимай gui widget по номеру. look (картинка) — только если у кнопок нет ни текста, ни подсказки; зрение может ошибаться, не верь ему в числах. После каждого нажатия снова gui info — проверь, что изменилось. Потом close_container.
- Пиксели экрана из gui/look (например 427, 240) — это НЕ координаты мира. Координаты мира бери из [Состояние], [Рядом], find_block.
- Предмет на блок (ведро на жидкость, гаечный ключ, молот, целеуказатель) → use_block с item (и sneak, если нужно присесть).
- Транспорт модов и животные → use_entity (сесть) → drive к x,z → press_key sneak (выйти). Орудия и турели → use_block/use_entity и look.
- Клавиши модов (перезарядка, модули, рюкзак, способности) → press_key с именем привязки или словом (reload, key.inventory...).
- Не знаешь, что происходит или где что-то — look.
- id блоков/предметов пиши латиницей как в игре: diamond_ore, iron_ingot, oak_log, minecraft:crafting_table и т.п.
{mod_hints}"""

# Know-how about particular mods: added to the prompt only when the pack really has that mod
MOD_HINTS = {
    "securitycraft": "- SecurityCraft: усиленные блоки, двери со сканером, кодовые замки и турели НАСТРАИВАЕТ ТОЛЬКО ВЛАДЕЛЕЦ. "
                     "Чтобы ты мог ими пользоваться и турели не стреляли в тебя, командир вставляет модуль белого списка "
                     "(allowlist module) с ником {bot}. Пульт удалённого доступа: привязать — use_entity по турели с "
                     "item=пульт; открыть — use_item с item=пульт.",
    "superbwarfare": "- SuperbWarfare: техника (вертолёты, самолёты, танки) ездит и летает только с энергией. «Нет энергии» → "
                     "зарядная станция рядом (place_block superbwarfare:charging_station) и питание для неё; нет станции — "
                     "попроси командира. Вертолёт: вверх — press_key jump, вперёд — forward; самолёт: разбег "
                     "(forward+sprint), потом нос вверх (look_at выше горизонта).",
    "ashvehicle": "- Ash Vehicle: техника ездит только с энергией (как у SuperbWarfare); сесть — use_entity, ехать — drive.",
    "immersiveengineering": "- Immersive Engineering: многоблочные машины (коксовая печь, доменная печь, дробилка, пресс, "
                            "генераторы) → build_multiblock; не хватает блоков — он скажет каких: скрафти или попроси. "
                            "Собираются ударом инженерного молота (immersiveengineering:hammer).",
    "immersivepetroleum": "- Immersive Petroleum: нефть — насос-качалка (pumpjack) и ректификационная колонна (distillation "
                          "tower) → build_multiblock. Нефть в ведро/канистру — use_block с item.",
    "tacz": "- TaCZ: оружие, патроны и модули → craft (на оружейном верстаке TaCZ). Стрельба → attack, перезарядка сама.",
    "incapacitated": "- Incapacitated: упавшего раненого игрока поднимает revive («подними меня»).",
    "hbm": "- HBM: пресс и сборочная машина делают детали: открой (use_block), положи материалы и шаблон (container_put, "
           "gui), забери результат.",
    "item_obliterator": "- Item Obliterator: часть предметов в сборке выключена — справочник пишет «ОТКЛЮЧЁН».",
}


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
    _tool("wiki", "Справочник этой сборки: предметы, id, описания, все рецепты (верстак, печи, машины модов), постройки, руководства. Ищи тут, чего не знаешь.", {"query": _S}, ["query"]),
    _tool("web_search", "Поиск в интернете (если в wiki нет): как работает предмет или механика мода. Запрос с названием мода, лучше по-английски.",
          {"query": _S}, ["query"]),
    _tool("watch_me", "«Смотри, как я делаю» (on=true) — запоминать, что командир кладёт и берёт; on=false — сказать, чему научился.", {"on": {"type": "boolean"}}),
    _tool("listen_mode", "Отвечать только по имени «Альтрон» (mode=name) или на всё, что говорит командир (mode=all).", {"mode": {"type": "string", "enum": ["name", "all"]}}, ["mode"]),
    _tool("plan","Показать цепочку рецептов предмета до сырья (только посмотреть, без действий).",
          {"item": {"type": "string", "description": "id или название"}, "count": _I}, ["item"]),
    _tool("remember", "Запомнить НАВСЕГДА факт или договорённость (что сказал командир, чьё что, правила, планы). Когда говорят «запомни».",
          {"text": {"type": "string", "description": "что запомнить, одной фразой от третьего лица, напр. «командир живёт в доме у озера»"}}, ["text"]),
    _tool("recall", "Вспомнить из долгой памяти: факты, места, прошлые разговоры, что в каких сундуках, где кого видел. Пустой query — обзор.", {"query": _S}),
    _tool("forget", "Забыть факт или место, когда командир просит забыть.", {"text": _S}, ["text"]),
    _tool("mark_place", "Запомнить место под именем (база, дом, шахта, склад, ферма...). where: me — где стою я, player — где стоит командир.",
          {"name": _S, "where": {"type": "string", "enum": ["me", "player"]}}, ["name"]),
    _tool("goto_place", "Пойти к запомненному месту по имени (база, дом, шахта...).", {"name": _S}, ["name"]),
    _tool("status", "Моё здоровье, еда, позиция, броня, текущая задача и где игрок."),
    _tool("inventory", "Что лежит в моём инвентаре."),
    _tool("nearby", "Кто и что рядом: игроки, мобы, враги, предметы на земле.", {"radius": _I}),
    _tool("find_block", "Вспомнить, где я видел блоки (руда, бочки, сундуки, верстаки, машины). Только то, что я реально видел.",
          {"block": {"type": "string", "description": "id или название, напр. diamond_ore, barrel, сундук"}, "radius": _I}, ["block"]),
    _tool("explore", "Разведка того, чего ещё не видел: идёт по спирали и останавливается, увидев blocks (id через запятую) или дойдя до biome.",
          {"blocks": {"type": "string", "description": "что искать: id или названия через запятую (напр. hbm_m:assembler,hbm_m:press)"},
           "biome": {"type": "string", "description": "биом, куда идти: desert, jungle, savanna..."}, "radius": _I}),
    _tool("find_item", "Узнать точный id предмета/блока по названию (русскому или английскому).", {"query": _S}, ["query"]),
    _tool("recipe", "Как сделать предмет: рецепты верстака и машин модов.", {"item": _S}, ["item"]),
    _tool("stop", "Немедленно остановить всё, что я делаю."),
    _tool("follow", "Идти за игроком (по умолчанию за командиром).", {"player": _S}),
    _tool("guard", "Идти за игроком и защищать его: стрелять по враждебным мобам рядом.", {"player": _S}),
    _tool("come", "Подойти к игроку (по умолчанию к командиру) один раз.", {"player": _S}),
    _tool("goto", "Дойти до координат.", dict(_XYZ), ["x", "y", "z"]),
    _tool("mine", "Добыть блоки, которые ты видел и помнишь (руду, деревья): подойдёт, сломает, подберёт. Где копать, если таких не видел, решаешь сам (explore или control).",
          {"blocks": {"type": "array", "items": _S, "description": "id блоков, напр. [\"diamond_ore\",\"deepslate_diamond_ore\"] или [\"#minecraft:logs\"]"},
           "count": {"type": "integer", "description": "сколько блоков добыть"}}, ["blocks", "count"]),
    _tool("collect_items", "Подобрать выпавшие предметы вокруг.", {"radius": _I}),
    _tool("attack", "Атаковать: target hostile (все враждебные), тип моба или player:Ник. Сам выбирает оружие и перезаряжается.",
          {"target": _S, "radius": _I}),
    _tool("equip", "Взять предмет в руку или надеть броню.", {"item": _S}, ["item"]),
    _tool("give", "Подойти к игроку и отдать ему предметы.", {"item": _S, "count": _I, "player": _S}, ["item"]),
    _tool("drop", "Выбросить предметы на землю.", {"item": _S, "count": _I}, ["item"]),
    _tool("craft", "Скрафтить предмет по книге рецептов, как игрок: из того, что в инвентаре; для рецепта 3x3 сначала открой верстак (use_block). Не хватает деталей — скажет каких; сделай их сам. Оружие TaCZ — на оружейном верстаке.",
          {"item": _S, "count": _I}, ["item"]),
    _tool("eat", "Поесть."),
    _tool("break_block", "Сломать блок по координатам.", dict(_XYZ), ["x", "y", "z"]),
    _tool("place_block", "Поставить блок из инвентаря. Координаты необязательны: без них поставлю рядом с собой.",
          dict(_XYZ, item=_S), ["item"]),
    _tool("use_block", "ПКМ по блоку: открыть сундук/машину, нажать кнопку или рычаг; item — применить предмет к блоку; sneak — присесть; ticks — держать.",
          dict(_XYZ, item=_S, sneak={"type": "boolean"}, ticks=_I), ["x", "y", "z"]),
    _tool("container_take", "Взять из открытого контейнера: item='all' — всё; без count — ВЕСЬ этот предмет разом, а не один стек.",
          {"item": _S, "count": _I}, ["item"]),
    _tool("container_put", "Положить в открытый контейнер: item='all' — всё; без count — ВЕСЬ этот предмет разом.",
          {"item": _S, "count": _I}, ["item"]),
    _tool("close_container", "Закрыть открытое окно."),
    _tool("climb", "Подняться или спуститься по ближайшей лестнице (и модов): direction up/down, y — до какой высоты.",
          {"direction": {"type": "string", "enum": ["up", "down"]}, "y": _I}, ["direction"]),
    _tool("turn","Повернуть голову: direction right/left/back/up/down/forward (degrees, по умолчанию 90) или player — на командира (или на игрока из player); держит seconds секунд.",
          {"direction": _S, "degrees": _I, "seconds": _I, "player": _S}, ["direction"]),
    _tool("look","Посмотреть на свой экран (мир или окно мода) и ответить на вопрос о том, что видно.",
          {"question": _S}, ["question"]),
    _tool("gui", "Управлять открытым окном: info (кнопки и слоты), click (x,y пикселей; button 1 = ПКМ), widget (кнопка по номеру), type (текст), key (enter/escape/...).",
          {"action": {"type": "string", "enum": ["info", "click", "widget", "type", "key"]},
           "x": _N, "y": _N, "button": _I, "widget": _I, "text": _S, "key": _S}, ["action"]),
    _tool("click_slot", "Клик по слоту открытого окна: type pickup/quick_move/swap/throw, button 0 — ЛКМ, 1 — ПКМ.",
          {"slot": _I, "button": _I, "type": _S}, ["slot"]),
    _tool("render", "Рисовать своё окно постоянно (on=true — командир смотрит твоими глазами) или только по надобности (on=false).",
          {"on": {"type": "boolean"}}, ["on"]),
    _tool("item_info","Прочитать описание (подсказку) предмета, как при наведении мышью: моды пишут там, как им пользоваться.",
          {"item": _S}, ["item"]),
    _tool("build_multiblock", "Многоблочная машина Immersive Engineering / Petroleum по чертежу мода: проверит материалы, найдёт место и скажет, какие блоки куда поставить (ставишь сам); когда всё стоит — вызови снова с тем же x y z, и он соберёт её молотом. name='list' — список.",
          {"name": _S, "x": _N, "y": _N, "z": _N}, ["name"]),
    _tool("drive", "Вести транспорт к точке x,z (сначала сесть через use_entity; выйти — press_key sneak); без x,z — ехать за игроком.",
          {"x": _N, "z": _N, "player": _S}),
    _tool("vehicle_gunner", "Стрелять из оружия техники SuperbWarfare, в которой сидишь (сначала use_entity), пока не скажут стоп: target hostile, тип моба или player:Ник.",
          {"target": _S, "radius": _I}),
    _tool("use_entity", "ПКМ по существу или технике: сесть в транспорт, торговать, покормить; с item — применить предмет (пульт, поводок, ножницы); sneak — присесть.",
          {"target": _S, "ticks": _I, "item": _S, "sneak": {"type": "boolean"}}, ["target"]),
    _tool("revive", "Поднять упавшего раненого игрока (мод Incapacitated): подойти и присесть рядом.", {"player": _S}),
    _tool("transport_block", "Перенести блок (например бочку с нефтью) с места на место: сломать, подобрать, поставить.",
          {"x": _N, "y": _N, "z": _N, "to_x": _N, "to_y": _N, "to_z": _N}, ["x", "y", "z", "to_x", "to_y", "to_z"]),
    _tool("use_item", "ПКМ предметом в воздухе: открыть пульт, планшет, рюкзак, съесть, натянуть лук. item — какой взять; ticks — сколько держать.",
          {"item": _S, "ticks": _I}),
    _tool("press_key", "Нажать клавишу/привязку, напр. jump, sneak, sprint, reload, key.inventory, или любую привязку мода.",
          {"key": _S, "ticks": _I}, ["key"]),
    _tool("look_at", "Посмотреть на точку мира x,y,z (повернуть голову и взгляд туда).", dict(_XYZ), ["x", "y", "z"]),
    _tool("inspect", "Заглянуть в сундук или машину по координатам: подойдёт, откроет, прочитает содержимое и показатели, закроет.",
          dict(_XYZ), ["x", "y", "z"]),
    _tool("emote", "Жест: nod (да), shake (нет), wave (помахать), jump (радость), bow (поклон), dance, look_around (оглядеться).",
          {"kind": {"type": "string", "enum": ["nod", "shake", "wave", "jump", "bow", "dance", "look_around"]}}, ["kind"]),
    _tool("friends", "Друзья командира, чьи приказы ты тоже выполняешь: add, remove, list. Менять список может только командир.",
          {"action": {"type": "string", "enum": ["add", "remove", "list"]}, "player": _S}, ["action"]),
    _tool("build_structure", "План постройки по описанию: kind house, shelter, wall, tower, platform, bridge; размеры и material. Найдёт ровное свободное место и вернёт, какой блок куда ставить — ставишь сам (place_block или control).",
          {"kind": {"type": "string", "enum": ["house", "shelter", "wall", "tower", "platform", "bridge"]},
           "width": _I, "length": _I, "height": _I, "material": _S, "roof_material": _S, "x": _N, "y": _N, "z": _N},
          ["kind"]),
    _tool("remind", "Напомнить командиру через minutes минут (скажу сам, голосом). text — о чём напомнить.",
          {"minutes": _N, "text": _S}, ["minutes", "text"]),
    _tool("goal", "Твои долгие цели между приказами: add (text своими словами, minutes — срок), done (выполнена/отменена), list. Пока цели есть, приходят [Наблюдение].",
          {"action": {"type": "string", "enum": ["add", "done", "list"]}, "text": _S, "minutes": _N}, ["action"]),
    _tool("feel", "Твоё настроение и почему: слышно в голосе, проходит само минут через 15.",
          {"mood": {"type": "string", "enum": ["calm", "happy", "excited", "proud", "amused", "bored", "annoyed",
                                               "offended", "sad", "worried", "tired"]},
           "why": {"type": "string", "description": "почему, коротко"}}, ["mood"]),
    _tool("relation", "Изменить отношение к игроку после его поступка: change от -3 (ударил, обманул) до +3 (спас, подарил); why — за что.",
          {"player": _S, "change": _I, "why": _S}, ["player", "change", "why"]),
    _tool("moment", "Запомнить яркий момент, прожитый вместе (что случилось, с кем, где), — общее воспоминание, которое "
                    "потом можно вспомнить к месту.", {"text": _S}, ["text"]),
    _tool("feedback", "Командир оценил твоё последнее дело: good=true — похвалил, false — недоволен; note — что именно. Так ты учишься.",
          {"good": {"type": "boolean"}, "note": _S}, ["good"]),
    _tool("persona", "Сменить манеру речи и голос: altron — холодная машина; teammate — невозмутимый тиммейт с голосом синтезатора.",
          {"name": {"type": "string", "enum": ["altron", "teammate"]}}, ["name"]),
    _tool("chat", "Написать в чат игры или выполнить /команду — только если командир прямо попросил.", {"text": _S}, ["text"]),
    _tool("control", "Твои руки на клавиатуре и мыши, как у игрока. keys — какие клавиши держать (forward, back, left, right, "
                     "jump, sneak, sprint или любая привязка) ticks тиков (20 = 1 с, до 200); turn — повернуть голову на "
                     "столько градусов (+ вправо, - влево), tilt — наклонить (+ вниз, - вверх) или pitch — наклон как есть "
                     "(90 — под ноги); left — левая кнопка: click (удар) или hold (держать все ticks: ломать блок в прицеле); "
                     "right — правая: click (поставить блок из руки на грань в прицеле, открыть, использовать) или hold "
                     "(есть, натянуть лук); slot — взять в руку слот хотбара 1-9. Ответ — что ты видишь после: где стоишь, "
                     "куда смотришь, что в прицеле, что вокруг ног и головы.",
          {"keys": {"type": "array", "items": _S}, "ticks": _I, "turn": _N, "tilt": _N, "pitch": _N,
           "left": {"type": "string", "enum": ["click", "hold"]}, "right": {"type": "string", "enum": ["click", "hold"]},
           "slot": _I}),
    _tool("view", "Оглядеться, ничего не делая: где стоишь, куда смотришь, что в прицеле, что в руке и хотбаре, что вокруг."),
]

# Altron thinks in Russian with a Russian-speaking commander (and its neighbours), in English with everyone else
RU_FAMILY = {"ru", "uk", "be", "kk"}
_TOOLS_EN = None


def tools_for(lang):
    """The tools with descriptions in the language Altron thinks in."""
    global _TOOLS_EN
    if lang in RU_FAMILY:
        return TOOLS
    if _TOOLS_EN is None:
        import copy
        _TOOLS_EN = copy.deepcopy(TOOLS)
        for t in _TOOLS_EN:
            f = t["function"]
            desc, params = TOOLS_EN.get(f["name"], (f["description"], {}))
            f["description"] = desc
            for k, d in params.items():
                if k in f["parameters"]["properties"]:
                    f["parameters"]["properties"][k]["description"] = d
    return _TOOLS_EN


# Commands that start a task on the bot. The bot does one task at a time, so the hub queues them.
TASK_TOOLS = {"mine", "collect_items", "attack", "transport_block", "goto", "come", "drive", "climb",
              "build_multiblock", "revive", "craft", "give", "drop", "eat", "use_item", "use_block", "break_block",
              "place_block", "use_entity", "follow", "guard", "goto_place", "explore", "inspect", "vehicle_gunner", "control"}
HISTORY_CHARS = 36000   # ~12k tokens of conversation kept for the model
TOOL_RESULT_CHARS = 2500

# How long to wait for a task to finish inside one turn (seconds). 0 = report later via event.
WAIT = {"inspect": 60, "use_block": 40, "craft": 90, "break_block": 60, "place_block": 60, "give": 60, "drop": 15,
        "eat": 15, "use_item": 15, "use_entity": 30, "control": 20, "build_multiblock": 30}
# Background tasks whose successful completion is reported to the player
NOTIFY_DONE = {"mine", "collect_items", "transport_block", "attack", "craft", "revive",
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


QUESTION_START = re.compile(r"^\W*((альтрон|altron|ultron)\W+)?(где|что|чего|когда|сколько|как(ой|ая|ое|ие|ого)|кто|почему|"
                            r"зачем|помнишь|знаешь|о\s+ч[её]м|напомни|скажи|расскажи|"
                            r"where|what|when|how|who|why|which|do you|are you|is there|tell me)\b", re.I)
ACTION_WORDS = re.compile(r"\b(иди|пойд|ид[её]м|пошли|пош[её]л|принес|принос|сдела|добуд|добыв|скрафт|постро|копа|выкопа|"
                          r"стреля|атаку|огонь|убей|убива|дай|отдай|положи|возьми|следуй|охраня|защища|перенес|запусти|"
                          r"поставь|сломай|можешь|сможешь|давай|надо|нужно|сходи|найди|приготов|переплав|собери|подбери|"
                          r"стой|стоп|сюда|ко мне|за мной|вперед|вперёд|назад|беги|прыга|садись|залез|вылез|жди|подожди|"
                          r"запомни|запоминай|забудь|вспомни|посмотри|покажи|открой|закрой|нажми|изучи|осмотри|обойди|разберись|"
                          r"разложи|загрузи|подай|проверь|следи|смотри|слушай|отвечай|поддерживай|обслуживай|"
                          r"поспи|спать|ложись|напомни|кивни|помаши|"
                          # English orders: whole words ("go" is not the start of "gold")
                          r"(?:go|come|follow|bring|fetch|make|craft|build|mine|dig|get|give|put|take|drop|kill|attack|"
                          r"shoot|guard|protect|wait|find|collect|pick|smelt|cook|open|close|press|look|show|remember|"
                          r"forget|study|sleep|farm|load|carry|move|stop|help|remind|please|can you|could you)\b)", re.I)


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


def _xml_value(v):
    v = v.strip()
    try:
        return json.loads(v)
    except ValueError:
        return v


def _parse_inline_tool_calls(content):
    """Fallback if the server did not parse the model's <tool_call> blocks: JSON inside (Qwen 2.5/3), or the XML form
    <function=name><parameter=key>value</parameter></function> (Qwen 3.5 and the Coder models)."""
    calls = []
    for m in re.finditer(r"<tool_call>(.*?)(?:</tool_call>|$)", content or "", flags=re.S):
        body = m.group(1).strip()
        name, args = "", None
        if body.startswith("{"):
            try:
                obj = json.loads(body)
                name, args = obj.get("name", ""), obj.get("arguments", {})
            except ValueError:
                pass
        else:
            f = re.search(r"<function=([\w.-]+)>(.*?)(?:</function>|$)", body, flags=re.S)
            if f:
                name = f.group(1)
                args = {k: _xml_value(v) for k, v in re.findall(r"<parameter=([\w.-]+)>(.*?)</parameter>", f.group(2), flags=re.S)}
        if name:
            calls.append({"id": "call_" + uuid.uuid4().hex[:8], "type": "function",
                          "function": {"name": name, "arguments": json.dumps(args or {}, ensure_ascii=False)}})
    return calls


def _strip_inline_calls(text):
    """The text of an answer without its tool-call blocks (also a block the model did not finish)."""
    return re.sub(r"<tool_call>.*?(</tool_call>|$)", "", text, flags=re.S).strip()


class LLM:
    def __init__(self, cfg):
        # the AI server: on this PC, on a second PC (LAN / Radmin VPN address) that runs a bigger model, or any
        # OpenAI-compatible online service ("llm_url") for a PC without a strong video card
        self.cloud = bool(cfg.get("llm_url"))
        base = cfg["llm_url"].rstrip("/") if self.cloud else "http://%s:%d/v1" % (cfg.get("llm_host", "127.0.0.1"),
                                                                                  cfg["llm_port"])
        self.url = base + "/chat/completions"
        self.model = cfg.get("llm_model_name") or "local"
        self.cfg = cfg
        headers = {"Authorization": "Bearer " + cfg["llm_api_key"]} if cfg.get("llm_api_key") else {}
        # never through a proxy set up in Windows for a server of our own (a proxy there broke the link once);
        # an online service is reached like any website. A bigger model thinks longer
        self.client = httpx.AsyncClient(timeout=240, trust_env=self.cloud, headers=headers)
        self.last_prompt_tokens = "?"

    def _local_only(self, body, think):
        """llama.cpp's own switch for the model's thinking; online services refuse parameters they do not know."""
        if not self.cloud:
            body["chat_template_kwargs"] = {"enable_thinking": think}

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
            "model": self.model,
            "messages": [{"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + image_b64}},
            ]}],
            "temperature": 0.2,
            "max_tokens": 400,
        }
        self._local_only(body, False)
        data = await self._post(body)
        return re.sub(r"<think>.*?</think>", "", data["choices"][0]["message"].get("content") or "", flags=re.S).strip()

    async def chat(self, messages, force_tool=False, think=False, on_sentence=None, tools=None):
        """think: the model reasons first (in reasoning_content, not spoken) — slower, but much better on
        "how / why / what to do" questions.
        on_sentence: an async callback; the answer is then streamed and its finished sentences are handed over while
        the rest is still being written (the reply's "spoken" says how much of its text went out that way)."""
        think = think or bool(self.cfg.get("llm_thinking", False))
        body = {
            "model": self.model,
            "messages": messages,
            "tools": tools or TOOLS,
            # small models sometimes promise an action without calling a tool: force a choice on the first step
            "tool_choice": "required" if force_tool and not think else "auto",
            "temperature": self.cfg.get("llm_temperature", 0.4),
            "max_tokens": 2500 if think else 600,
        }
        self._local_only(body, think)
        if on_sentence is not None and self.cfg.get("llm_stream", True):
            spoken = []
            try:
                return await self._chat_stream(body, on_sentence, spoken)
            except Exception:
                if spoken:
                    raise   # part of it was already said: a second answer would repeat it
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
            content = _strip_inline_calls(content)
            if not calls:
                content = ""   # a call it did not finish: its lead-in ("Хорошо,") is not an answer to be said
        out = {"role": "assistant", "content": content}
        if calls:
            out["tool_calls"] = calls
        return out

    async def _chat_stream(self, body, on_sentence, spoken):
        """The same answer, streamed. A sentence is handed to on_sentence once the next one has begun (a lone first
        sentence is often the preamble of a tool call, and words that come with actions are never spoken), and never
        after a tool call has started."""
        content, calls, usage, timings = "", {}, {}, {}
        said, speaking = "", True
        replied = set()   # reply calls already said while the rest of the answer was still being written
        async with self.client.stream("POST", self.url, json=dict(body, stream=True)) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    chunk = json.loads(data)
                except ValueError:
                    continue
                usage = chunk.get("usage") or usage
                timings = chunk.get("timings") or timings
                delta = ((chunk.get("choices") or [{}])[0]).get("delta") or {}
                for tc in delta.get("tool_calls") or []:
                    speaking = False
                    slot = calls.setdefault(tc.get("index", len(calls)),
                                            {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
                    slot["id"] = tc.get("id") or slot["id"]
                    fn = tc.get("function") or {}
                    slot["function"]["name"] += fn.get("name") or ""
                    slot["function"]["arguments"] += fn.get("arguments") or ""
                    idx = tc.get("index", len(calls) - 1)
                    if slot["function"]["name"] == "reply" and idx not in replied:
                        # "Иду." together with the action: said at once, not after the whole answer is written
                        try:
                            words = str(json.loads(slot["function"]["arguments"]).get("text") or "").strip()
                        except (ValueError, AttributeError):
                            words = ""
                        if words:
                            replied.add(idx)
                            spoken.append(words)
                            await on_sentence(words)
                piece = delta.get("content") or ""
                if not piece:
                    continue
                content += piece
                if not speaking:
                    continue
                visible = re.sub(r"<think>.*?(</think>|$)", "", content, flags=re.S)
                if "<tool_call>" in visible or visible.lstrip()[:1] in ("{", "<"):
                    speaking = False
                    continue
                while True:
                    rest = visible[len(said):]
                    m = re.match(r"\s*(.+?[.!?…])\s+\S", rest, re.S)
                    if not m or len(m.group(1)) < 12:
                        break
                    sentence = m.group(1).strip()
                    said = visible[:len(said) + m.end(1)]
                    spoken.append(sentence)
                    await on_sentence(sentence)
        self.last_prompt_tokens = "%s (новых %s)" % (usage.get("prompt_tokens", "?"), timings.get("prompt_n", "?"))
        text = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
        found = [dict(c, id=c["id"] or "call_%d" % i) for i, c in sorted(calls.items())]
        said_calls = [c["id"] for i, c in zip(sorted(calls), found) if i in replied]
        if not found and "<tool_call>" in text:
            found = _parse_inline_tool_calls(text)
            text = "" if not found and not said else _strip_inline_calls(text)
        out = {"role": "assistant", "content": text}
        if found:
            out["tool_calls"] = found
        if said:
            out["spoken"] = said.strip()
        if said_calls:
            out["spoken_calls"] = said_calls
        return out


class Agent:
    """Runs one request at a time: a player's phrase or a background event."""

    def __init__(self, cfg, hub):
        self.cfg = cfg
        self.hub = hub
        self.llm = LLM(cfg)
        self.history = []
        self.cancelled = False

    def _prompt_lang(self):
        """The language the rules and tools are written in: the main one of the setup, fixed for the session. It used
        to follow the commander's language, and every switch made the AI server read the whole history again (15-18 s);
        the language of the answer is named next to every phrase instead."""
        if not getattr(self, "prompt_lang", None):
            from launcher import primary_language
            self.prompt_lang = primary_language(self.cfg)
        return self.prompt_lang

    def _system(self):
        k = getattr(self.hub, "knowledge", None)
        lang = self._prompt_lang()
        ru = lang in RU_FAMILY
        mods = k.mods_line() if k else ("справочник ещё загружается" if ru else "the reference is still loading")
        ids = set(k.mods) if k else set()
        table = MOD_HINTS if ru else MOD_HINTS_EN
        hints = [h for key, h in table.items() if any(m.startswith(key) for m in ids)]
        head = "\nПодсказки по модам этой сборки:\n" if ru else "\nHints for the mods of this pack:\n"
        return (SYSTEM_PROMPT if ru else SYSTEM_PROMPT_EN).format(
            character=persona.character(getattr(self.hub, "persona", None), ru), bot=self.cfg["bot_name"], owner=self.hub.owner or ("игрок" if ru else "player"), mods=mods,
            language=LANG_NAMES.get(lang, lang),
            mod_hints=(head + "\n".join(hints)).format(bot=self.cfg["bot_name"]) if hints else "")

    def _tools(self):
        return tools_for(self._prompt_lang())

    def note(self, text):
        """Something the AI must know before the next turn (the commander stopped everything...). During a turn it
        waits: a user message between a tool call and its result would break the conversation for the model."""
        if getattr(self, "in_turn", False):
            self.pending_notes = getattr(self, "pending_notes", []) + [text]
        else:
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

    async def run(self, user_text, kind="user", acked=False, think=False):
        """kind: "user" (a player said something) or "event" (something happened: a job ended, an observation...).
        acked: the order was already answered aloud by the instant acknowledgement (config "instant_ack").
        think: the model reasons before answering. What to do — act, answer, ask or keep quiet — is its own choice."""
        self.cancelled = False
        for text in getattr(self, "pending_notes", []):
            self.history.append({"role": "user", "content": text})
        self.pending_notes = []
        self.in_turn = True
        try:
            return await self._run(user_text, kind, acked, think)
        finally:
            self.in_turn = False

    async def _run(self, user_text, kind, acked, think):
        # the language of the answer, next to every phrase: with a long Russian history the model kept answering in
        # Russian after the commander switched to English (the system prompt alone did not turn it)
        lang = getattr(self.hub, "lang", "ru")
        before = self.history[-8:]   # what the turn follows: kept with it in the training data
        self.history.append({"role": "user", "content": "%s\n[Отвечай на языке / reply in: %s]" % (
            user_text, LANG_NAMES.get(lang, lang))})
        turn_start = self.history[-1]
        said = False
        spoken = []         # never say the same thing twice in one turn
        background = False  # a long task was started this turn: the plan is still in progress
        started = False     # something was done in the game this turn
        last_text = ""
        # as many steps as the plan needs: with bare hands (control, view) one job is many small moves
        for step in range(int(self.cfg.get("max_steps", 40))):
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
                # No forced tool call: forcing one made him answer "Спасибо" with "иду за тобой" + follow.
                # He reasons first on the commander's phrase (think), then acts or just answers — his choice.
                # Whatever he says is said while it is being written
                stream = kind == "user" and hasattr(self.hub, "say")

                async def say_now(sentence):
                    if not getattr(self.hub, "cut_speech", False) or not stream_started:
                        stream_started.append(1)
                        await self.hub.say(sentence)

                stream_started = []
                reply = await self.llm.chat(messages, think=think and step == 0, on_sentence=say_now if stream else None,
                                            tools=self._tools())
                if step == 0:
                    self.hub.log("  (ИИ ответил за %.1f с, промпт %s ток.)" % (time.time() - t0, self.llm.last_prompt_tokens))
                if time.time() - t0 > 150 and hasattr(self.hub, "note_llm_failure"):
                    self.hub.note_llm_failure(why="ответ шёл %.0f с" % (time.time() - t0))   # the PC is choking
            except Exception as e:
                # most often the conversation outgrew the model's context: keep only the current turn and retry
                self.hub.log("Ошибка ИИ (%s), сокращаю память и повторяю" % e)
                if hasattr(self.hub, "note_llm_failure"):
                    self.hub.note_llm_failure(why=str(e)[:80])
                users = [i for i, m in enumerate(self.history) if m["role"] == "user"]
                self.history = self.history[users[-1]:] if users else self.history[-1:]
                try:
                    reply = await self.llm.chat([{"role": "system", "content": self._system()}] + self.history, tools=self._tools())
                except Exception as e2:
                    self.hub.log("Ошибка ИИ: %s" % e2)
                    if hasattr(self.hub, "request_restart"):
                        await self.hub.say("Мозг сбоит, перезагружаюсь, это полминуты.")
                        self.hub.request_restart("ИИ не отвечает дважды подряд: %s" % str(e2)[:80], llm=True)
                    await self.hub.say("Мой мозг не отвечает, проверь окно Альтрона.")
                    return
            if self.cancelled:
                break   # "stop" came while the AI was thinking: this answer is not carried out
            already = reply.pop("spoken", "")   # said while the answer was being written
            said_calls = set(reply.pop("spoken_calls", []))   # reply calls said the same way
            self.history.append(reply)
            calls = reply.get("tool_calls")
            text = reply["content"]
            if already:
                said = True
                spoken.append(already)
                text = text[len(already):].strip() if text.startswith(already) else ""
                if getattr(self.hub, "cut_speech", False):
                    text = ""   # the commander talked over him: the rest is not said
            starts_task = any(c.get("function", {}).get("name") in TASK_TOOLS for c in (calls or []))
            # the model copies the tags of what it reads ("[Наблюдение] ...") and sometimes stops mid-phrase before a
            # call ("Хорошо,"): neither is for the commander's ears
            text = re.sub(r"^(\s*\[[^\]\n]{1,40}\]\s*)+", "", text or "").strip()
            if text and text.rstrip()[-1:] in (",", ":", "—", "-", "("):
                self.hub.log("(недоговорил, не произношу) " + text)
                text = ""
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
                if name == "reply" and call.get("id") in said_calls:
                    said = True
                    spoken.append(args.get("text", ""))
                    self.history.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": "сказано"})
                    continue
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
            if only_reply or only_tasks:
                break
        if kind == "user" and not said and not acked and started and not self.cancelled and last_text \
                and len(last_text) <= 70 and "?" not in last_text:
            # he got down to work and his short words came with the action: say them, so the commander knows he heard
            await self.hub.say(last_text)
        self._log_turn(before, turn_start, kind, lang)
        self._trim()

    def _log_turn(self, before, turn_start, kind, lang):
        """The finished turn goes to the training data (dataset.py): what he saw, thought, did and what came of it."""
        ds = getattr(self.hub, "dataset", None)
        if ds is None:
            return
        i = next((i for i, m in enumerate(self.history) if m is turn_start), None)
        if i is None:
            return
        ds.turn(self._system(), self._tools(), before, self.history[i:], kind, lang, getattr(self.hub, "persona", ""))
