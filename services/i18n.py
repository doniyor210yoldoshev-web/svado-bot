"""Localization loader.

Usage::

    from services.i18n import t, translator

    text = t(user["lang"], "menu.title")
    _ = translator(user["lang"])
    text = _("shop.price", price=fmt_sum(1000))
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from config import BASE_DIR, DEFAULT_LANG, SUPPORTED_LANGS

logger = logging.getLogger(__name__)

LOCALES_DIR = BASE_DIR / "locales"


class Translator:
    def __init__(self) -> None:
        self._data: dict[str, dict[str, str]] = {}
        self.reload()

    def reload(self) -> None:
        data: dict[str, dict[str, str]] = {}
        for lang in SUPPORTED_LANGS:
            path = LOCALES_DIR / f"{lang}.json"
            try:
                with path.open("r", encoding="utf-8") as handle:
                    data[lang] = json.load(handle)
            except FileNotFoundError:
                logger.error("Locale fayli topilmadi: %s", path)
                data[lang] = {}
            except json.JSONDecodeError:
                logger.exception("Locale fayli buzilgan: %s", path)
                data[lang] = {}
        self._data = data

    def normalize(self, lang: str | None) -> str:
        return lang if lang in SUPPORTED_LANGS else DEFAULT_LANG

    def t(self, lang: str | None, key: str, **kwargs: object) -> str:
        lang = self.normalize(lang)
        value = self._data.get(lang, {}).get(key)
        if value is None:
            value = self._data.get(DEFAULT_LANG, {}).get(key)
        if value is None:
            logger.warning("Tarjima topilmadi: %s (%s)", key, lang)
            return key
        if kwargs:
            try:
                return value.format(**kwargs)
            except (KeyError, IndexError, ValueError):
                logger.exception("Tarjima formatlashda xatolik: %s", key)
        return value

    def translator(self, lang: str | None):
        lang = self.normalize(lang)

        def _get(key: str, **kwargs: object) -> str:
            return self.t(lang, key, **kwargs)

        return _get


_translator = Translator()


def t(lang: str | None, key: str, **kwargs: object) -> str:
    return _translator.t(lang, key, **kwargs)


def translator(lang: str | None):
    return _translator.translator(lang)


def normalize_lang(lang: str | None) -> str:
    return _translator.normalize(lang)


def reload_locales() -> None:
    _translator.reload()


def locale_path(lang: str) -> Path:
    return LOCALES_DIR / f"{lang}.json"
