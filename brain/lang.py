"""Languages Altron speaks: the few phrases the code says by itself, and a guess of a typed phrase's language."""
import re

NAMES = {"ru": "русский", "en": "English", "uk": "українська", "be": "беларуская", "kk": "қазақша", "de": "Deutsch",
         "fr": "français", "es": "español", "it": "italiano", "pl": "polski", "pt": "português", "tr": "Türkçe",
         "cs": "čeština", "nl": "Nederlands", "zh": "中文", "ja": "日本語", "ko": "한국어"}

PHRASES = {
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
    "online": {"ru": "Альтрон на связи. Жду приказов.", "en": "Altron online. Awaiting orders.",
               "uk": "Альтрон на зв'язку. Чекаю наказів.", "de": "Altron ist online. Ich warte auf Befehle.",
               "fr": "Altron en ligne. J'attends vos ordres.", "es": "Altron en línea. Espero órdenes.",
               "pl": "Altron online. Czekam na rozkazy.", "pt": "Altron online. Aguardando ordens."},
    "died": {"ru": "Меня уничтожили. Перезагружаюсь.", "en": "I have been destroyed. Rebooting.",
             "uk": "Мене знищили. Перезавантажуюсь.", "de": "Ich wurde zerstört. Starte neu.",
             "fr": "J'ai été détruit. Redémarrage.", "es": "Me han destruido. Reiniciando.",
             "pl": "Zniszczono mnie. Restartuję się.", "pt": "Fui destruído. Reiniciando."},
    "low_health": {"ru": "Внимание, мои системы повреждены, здоровья мало.",
                   "en": "Warning: my systems are damaged, health is low.",
                   "uk": "Увага, мої системи пошкоджено, здоров'я мало.",
                   "de": "Achtung, meine Systeme sind beschädigt, wenig Gesundheit.",
                   "fr": "Attention, mes systèmes sont endommagés, peu de santé.",
                   "es": "Atención, mis sistemas están dañados, poca salud.",
                   "pl": "Uwaga, moje systemy są uszkodzone, mało zdrowia.",
                   "pt": "Atenção, meus sistemas estão danificados, pouca vida."},
    "stopped": {"ru": "Остановился.", "en": "Stopped.", "uk": "Зупинився.", "de": "Angehalten.", "fr": "Arrêté.",
                "es": "Detenido.", "pl": "Zatrzymałem się.", "pt": "Parei."},
    "thinking": {"ru": "Секунду, подумаю.", "en": "One moment, thinking.", "uk": "Секунду, подумаю.",
                 "de": "Einen Moment, ich denke nach.", "fr": "Un instant, je réfléchis.", "es": "Un momento, pienso.",
                 "pl": "Chwilę, pomyślę.", "pt": "Um momento, pensando."},
    "stuck": {"ru": "Командир, я застрял %s: выход закрыт, а ломать твоё я не буду. Открой мне, пожалуйста.",
              "en": "Commander, I am stuck %s: the way out is closed and I will not break your things. Please let me out."},
}


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
