from __future__ import annotations

APP_NAME = "LION REBAR DESKTOP"
APP_VERSION = "0.1.44.23-rc46"
# Тестовата линия (C:\lr_exp, клон experiment) се различава от основната
# програма (rc44 на драйва) с ОТДЕЛНИ данни и видим знак в прозореца.
# Собственик, 08.10.2026: „да го виждам и различавам ясно от основната“.
# Причината за отделната папка: и двете версии пазят engine-а и сесиите си на
# лаптопа, под едно и също име — тестовата, пусната без START_TEST.bat,
# би писала в работната сесия и engine registry-то на основната. При преминаване в основната
# програма двете стойности се връщат СЪЗНАТЕЛНО: "" и "LionRebarDesktop".
APP_EDITION = "ТЕСТОВА ВЕРСИЯ (автоматизация)"
APP_DATA_FOLDER = "LionRebarDesktop_TEST"
RECOGNITION_MATCHER_SHA256 = (
    "dc3a660cd906a8167b38bf595e9b5d9d86095181e68203b0ae1c029d96766065"
)
ORGANIZATION = "Lion Build Ltd."
ENGINE_SCHEMA = "lion-rebar-engine/v1"
ENGINE_BUNDLE_SUFFIX = ".lionengine"
PROJECT_SCHEMA = "lion-rebar-project/v1"
PROJECT_FILENAME = "project.json"

APPROVAL_PHRASE = "ОДОБРЯВАМ ПУБЛИКУВАНЕТО"
ACTIVATION_PHRASE = "АКТИВИРАМ ВЕРСИЯТА"
CONTROLLED_REVISION_PHRASE = "СЪЗДАВАМ КОНТРОЛИРАНА РЕВИЗИЯ"

DEFAULT_CATEGORIES = [
    {"code": "BOTTOM", "sheet_name": "Долна", "aliases": ["Долна основна"]},
    {"code": "BOTTOM_EXTRA", "sheet_name": "Долна усилване", "aliases": ["Долна усилваща"]},
    {"code": "TOP", "sheet_name": "Горна", "aliases": ["Горна основна"]},
    {"code": "TOP_EXTRA", "sheet_name": "Горна усилване", "aliases": ["Горна усилваща"]},
    {"code": "DETAILS", "sheet_name": "Детайли", "aliases": []},
    {"code": "BEAMS", "sheet_name": "Греди", "aliases": []},
    {"code": "TOTAL", "sheet_name": "Общо", "aliases": []},
]

DEFAULT_POSITION_STATUSES = [
    "ПОТВЪРДЕНО",
    "ОДОБРЕНО РЪЧНО",
    "ЗА ПРОВЕРКА",
    "ЛИПСВАЩА ГЕОМЕТРИЯ",
    "НЕПОЗНАТА ФОРМА",
    "НЕСЪОТВЕТСТВИЕ В ДЪЛЖИНАТА",
    "ЕДНАКЪВ ТЕКСТ / РАЗЛИЧНА ГЕОМЕТРИЯ",
    "НЕЯСЕН МНОЖИТЕЛ",
    "НЕЯСНИ МЕРНИ ЕДИНИЦИ",
    "ПРОЕКТАНТСКА ГРЕШКА",
    "ОДОБРЕНА КОРЕКЦИЯ",
    "ВИЖ ЧЕРТЕЖ",
    "ИЗКЛЮЧЕНО",
]

SUPPORTED_DXF_ENTITY_TYPES = {
    "LINE",
    "LWPOLYLINE",
    "POLYLINE",
    "CIRCLE",
    "ARC",
    "TEXT",
    "MTEXT",
    "POINT",
    "INSERT",
    "DIMENSION",
    "LEADER",
    "HATCH",
    "BLOCK_ATTR_TEXT",
}

REQUIRED_PROJECT_DIRS = (
    "source",
    "master",
    "working",
    "output",
    "logs",
)

VALID_ENGINE_STATES = {"DRAFT", "PUBLISHED", "ARCHIVED"}
VALID_SHAPE_STATES = {"DRAFT", "APPROVED", "LOCKED", "RETIRED"}
VALID_BEND_DIRECTIONS = {
    "NONE",
    "LEFT",
    "RIGHT",
    "НЯМА",
    "НАЛЯВО",
    "НАДЯСНО",
}
VALID_INITIAL_DIRECTIONS = {
    "RIGHT",
    "LEFT",
    "UP",
    "DOWN",
    "НАДЯСНО",
    "НАЛЯВО",
    "НАГОРЕ",
    "НАДОЛУ",
}
