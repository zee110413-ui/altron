"""Languages Altron speaks: the very few phrases the code says by itself (everything else is the AI's own words), and a
guess of a typed phrase's language."""
import re

NAMES = {"ru": "русский", "en": "English", "uk": "українська", "be": "беларуская", "kk": "қазақша", "de": "Deutsch",
         "fr": "français", "es": "español", "it": "italiano", "pl": "polski", "pt": "português", "tr": "Türkçe",
         "cs": "čeština", "nl": "Nederlands", "zh": "中文", "ja": "日本語", "ko": "한국어"}

PHRASES = {
    # the instant "yes" to an order, before the AI has thought — only with "instant_ack": true in config.json
    "acks": {
        "ru": ("Есть, командир.", "Принял.", "Сделаю.", "Понял, командир."),
        "en": ("Yes, commander.", "Understood.", "Consider it done.", "On it, commander."),
        "uk": ("Так, командире.", "Прийняв.", "Зроблю.", "Зрозумів, командире."),
        "de": ("Jawohl, Kommandant.", "Verstanden.", "Wird erledigt.", "Bin dran, Kommandant."),
        "fr": ("Oui, commandant.", "Compris.", "C'est comme si c'était fait.", "J'y vais, commandant."),
        "es": ("Sí, comandante.", "Entendido.", "Hecho.", "En ello, comandante."),
        "pl": ("Tak jest, dowódco.", "Przyjąłem.", "Zrobię to.", "Już się robi, dowódco."),
        "pt": ("Sim, comandante.", "Entendido.", "Considere feito.", "Já vou, comandante."),
    },
    # the one reflex: a creeper explodes in 1.5 s, faster than the AI can think
    "creeper": {"ru": "%s, крипер рядом! Отойди!", "en": "%s, creeper next to you! Move!",
                "uk": "%s, кріпер поруч! Відійди!", "de": "%s, ein Creeper neben dir! Weg da!",
                "fr": "%s, un creeper à côté de toi ! Bouge !", "es": "¡%s, un creeper a tu lado! ¡Muévete!",
                "pl": "%s, creeper obok ciebie! Odsuń się!", "pt": "%s, creeper do seu lado! Sai daí!"},
    "commander": {"ru": "Командир", "en": "Commander", "uk": "Командире", "de": "Kommandant", "fr": "Commandant",
                  "es": "Comandante", "pl": "Dowódco", "pt": "Comandante"},
}


RU_FAMILY = {"ru", "uk", "be", "kk"}
_ui = ["ru"]


def set_ui(lang):
    """The brain's window speaks Russian with a Russian-speaking commander (and its neighbours), English otherwise."""
    _ui[0] = "ru" if lang in RU_FAMILY else "en"


def ui(ru, en):
    return ru if _ui[0] == "ru" else en


def phrase(key, lang):
    """The phrase in this language; English (then Russian) when it has no translation yet."""
    table = PHRASES[key]
    return table.get(lang) or table.get("en") or table["ru"]


def guess_lang(text, allowed, fallback):
    """Language of a typed phrase, by its alphabet, among the ones Altron is set to speak."""
    allowed = list(allowed or [])
    if re.search(r"[一-鿿]", text) and "zh" in allowed:
        return "zh"
    if re.search(r"[぀-ヿ]", text) and "ja" in allowed:
        return "ja"
    if re.search(r"[가-힯]", text) and "ko" in allowed:
        return "ko"
    if re.search(r"[а-яёіїєґў]", text, re.I):
        if re.search(r"[іїєґ]", text, re.I) and "uk" in allowed:
            return "uk"
        cyr = [c for c in allowed if c in ("ru", "uk", "be", "kk")]
        return cyr[0] if cyr else fallback
    if re.search(r"[a-z]", text, re.I):
        latin = [c for c in allowed if c not in ("ru", "uk", "be", "kk", "zh", "ja", "ko")]
        return latin[0] if latin else fallback
    return fallback
