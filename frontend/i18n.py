# PiStock — PLM/inventory tool for FreeCAD-based workshops
# Copyright (C) 2026 GA3Dtech
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# (...) See https://www.gnu.org/licenses/agpl-3.0.html

"""
Module i18n : chargement des traductions via gettext, avec fallback
sur un mini-parser .po pour permettre le demarrage sans .mo compile.

Workflow :
- En dev : on edite les .po, l'app charge les .po directement via le
  fallback parser inclus. Pas besoin de compiler a chaque modif.
- En prod : on compile les .po en .mo avec
    msgfmt locales/fr/LC_MESSAGES/messages.po -o locales/fr/LC_MESSAGES/messages.mo
  (ou pybabel compile -d locales). gettext utilise alors les .mo qui
  sont plus rapides a charger.

Usage :
    from i18n import _, set_lang, get_lang, AVAILABLE_LANGS
    label = _("Catalog")        # renvoie "Catalogue" si lang=fr

Convention : les msgid sont EN ANGLAIS. Les .po fr.po contiennent les
traductions. La langue 'en' n'a pas besoin de .po (msgid == msgstr).
"""
import os
import gettext as _gettext

# Repertoire ou se trouvent les locales (relativement a ce fichier).
LOCALES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "locales")
DOMAIN = "messages"

# Liste des langues supportees. La premiere est utilisee comme defaut
# si la preference n'est pas definie ailleurs.
AVAILABLE_LANGS = [
    ("en", "English"),
    ("fr", "Français"),
]
DEFAULT_LANG = "en"

# Cache : un GNUTranslations par langue, charge a la demande
_translations_cache: dict[str, _gettext.NullTranslations] = {}
# Langue active globale (sera ecrasee par set_lang)
_current_lang = DEFAULT_LANG


# ----------------------------------------------------------------------
#  MINI-PARSER .po — pour demarrer sans avoir a compiler les .mo
# ----------------------------------------------------------------------
def _parse_po_file(path: str) -> dict[str, str]:
    """Parse un .po basique en dict {msgid: msgstr}.
    Ne gere pas les pluriels, les contextes, ni les commentaires
    structures — juste les paires msgid/msgstr simples, ce qui suffit
    largement pour notre usage."""
    result: dict[str, str] = {}
    if not os.path.isfile(path):
        return result

    state = None        # 'id' / 'str' / None
    cur_id_parts: list[str] = []
    cur_str_parts: list[str] = []

    def flush():
        msgid = "".join(cur_id_parts)
        msgstr = "".join(cur_str_parts)
        # On ignore l'entree vide (en-tete de metadata du .po)
        if msgid and msgstr:
            result[msgid] = msgstr
        cur_id_parts.clear()
        cur_str_parts.clear()

    def unquote(s: str) -> str:
        # Retire les guillemets entourant et decode les echappements
        # \" \\ \n \t — suffisant pour des chaines UI normales.
        s = s.strip()
        if s.startswith('"') and s.endswith('"'):
            s = s[1:-1]
        return (s.replace('\\n', '\n')
                .replace('\\t', '\t')
                .replace('\\"', '"')
                .replace('\\\\', '\\'))

    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.rstrip("\n")
                stripped = line.strip()
                if not stripped or stripped.startswith("#"):
                    # Ligne vide ou commentaire : flush l'entree courante
                    if state is not None:
                        flush()
                        state = None
                    continue
                if stripped.startswith("msgid "):
                    if state is not None:
                        flush()
                    cur_id_parts.append(unquote(stripped[6:]))
                    state = "id"
                elif stripped.startswith("msgstr "):
                    cur_str_parts.append(unquote(stripped[7:]))
                    state = "str"
                elif stripped.startswith('"'):
                    # Suite multi-ligne d'un msgid ou msgstr
                    chunk = unquote(stripped)
                    if state == "id":
                        cur_id_parts.append(chunk)
                    elif state == "str":
                        cur_str_parts.append(chunk)
            # Flush final
            if state is not None:
                flush()
    except OSError:
        pass
    return result


class _DictTranslations(_gettext.NullTranslations):
    """Adapter qui expose un dict {msgid: msgstr} via l'API gettext."""
    def __init__(self, mapping: dict[str, str]):
        super().__init__()
        self._mapping = mapping

    def gettext(self, message: str) -> str:
        return self._mapping.get(message, message)


# ----------------------------------------------------------------------
#  CHARGEMENT DES TRADUCTIONS
# ----------------------------------------------------------------------
def _load_translation(lang: str) -> _gettext.NullTranslations:
    """Charge une langue : essaie le .mo (gettext standard, rapide),
    fallback sur le .po parse en Python."""
    if lang in _translations_cache:
        return _translations_cache[lang]

    # 1. Tentative .mo via gettext.translation
    try:
        t = _gettext.translation(DOMAIN, LOCALES_DIR,
                                  languages=[lang], fallback=False)
        _translations_cache[lang] = t
        return t
    except (FileNotFoundError, OSError):
        pass

    # 2. Fallback : parse .po directement
    po_path = os.path.join(LOCALES_DIR, lang, "LC_MESSAGES",
                            f"{DOMAIN}.po")
    mapping = _parse_po_file(po_path)
    t = _DictTranslations(mapping)
    _translations_cache[lang] = t
    return t


# ----------------------------------------------------------------------
#  API PUBLIQUE
# ----------------------------------------------------------------------
def set_lang(lang: str) -> None:
    """Definit la langue active globalement pour ce processus."""
    global _current_lang
    if lang not in {code for code, _label in AVAILABLE_LANGS}:
        lang = DEFAULT_LANG
    _current_lang = lang


def get_lang() -> str:
    """Renvoie la langue active."""
    return _current_lang


def _(message: str) -> str:
    """Traduit un message vers la langue active. Si pas de traduction
    disponible, retourne le message original (anglais par convention)."""
    return _load_translation(_current_lang).gettext(message)
