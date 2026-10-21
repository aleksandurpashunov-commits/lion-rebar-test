"""Прилага НАУЧЕНОТО върху нов чертеж — само когато чертежът го подкрепя.

Входът е отказ на паспорта: ``propose_convention_profile`` е видяло реално
доказателство в този слой, но примерите са под самостоятелния праг (например
„само 2 надписа изглеждат като заглавие на зона — под 3 не се предлага").

Наученото правило НЕ заменя доказателството, а само прага за количество:

* целевият слой трябва да даде поне ``MIN_TARGET_SUPPORT`` свои примера —
  нула или един е отказ, който се записва;
* признакът на името, ако правилото го носи, трябва да е същият — това
  СТЕСНЯВА правилото;
* за ``REBAR_GEOMETRY`` превесът над контролата трябва да е >= 4.0 и тук —
  защитата срещу случайно съвпадение не се отслабва.

Резултатът е РАЗРЕШЕН ОТКАЗ с произход: кое правило, от кои чертежи, с какво
доказателство. Ролята на слоя е правило за прочит — не одобрява позиция, не
определя форма и не пипа размери, бройки и одобрения.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping, Sequence

from lion_rebar.learning.convention_learner import (
    content_only_disabled_roles,
    CONTROL_FIELD,
    MIN_CONTROL_RATIO,
    MIN_TARGET_SUPPORT,
    SUPPORT_FIELD,
    name_token,
    signature_of,
)
from lion_rebar.learning.knowledge_store import ACCEPTED, KnowledgeStore, LearnedRule

APPLY_SCHEMA = "lion-rebar-learned-application/v1"

#: Наученото носи САМО прага за количество. Затова се прилага единствено върху
#: откази, които казват „примерите са малко" — не върху откази по същество.
#:
#: Измерено 27.09.2026: първата редакция на този модул разреши отказа за
#: ``ARMTXT`` в ``тест-004``, чиято причина е „два или повече тага носят само
#: стандартни диаметри" — тоест НЕЯСНОТА, не малко примери. Това е отмяна на
#: отказ по същество и е точно забраненото. Оттук нататък се разпознава видът на
#: отказа, а не само броят.
НЕДОСТАТЪЧЕН_БРОЙ = ("не се предлага", "под ")
ОТКАЗ_ПО_СЪЩЕСТВО = ("нито един", "два или повече", "контролат", "контролата",
                     "повече от една")


def count_shortage_refusal(reason: str) -> bool:
    """Отказът ли е „малко примери", а не неяснота или проваленa контрола."""

    текст = str(reason or "").lower()
    if any(игла in текст for игла in ОТКАЗ_ПО_СЪЩЕСТВО):
        return False
    return any(игла in текст for игла in НЕДОСТАТЪЧЕН_БРОЙ)


def _support(counts: Mapping[str, Any], role: str) -> int:
    поле = SUPPORT_FIELD.get(role)
    if not поле:
        return 0
    try:
        return int(counts.get(поле) or 0)
    except (TypeError, ValueError):
        return 0


def _control_matches(counts: Mapping[str, Any]) -> int:
    try:
        return int(counts.get(CONTROL_FIELD) or 0)
    except (TypeError, ValueError):
        return 0


def _control_ratio(counts: Mapping[str, Any], role: str) -> float | None:
    if role != "REBAR_GEOMETRY":
        return None
    return _support(counts, role) / max(1, _control_matches(counts))


def control_is_clean(counts: Mapping[str, Any], role: str) -> tuple[bool, str]:
    """Контролата: колко надписа биха „съвпаднали" с ИЗМЕСТЕНА дължина.

    Нула контролни съвпадения значи, че на този слой няма нито едно случайно
    попадение — най-силното възможно доказателство. Старата проверка делеше на
    ``max(1, контролни)`` и затова слой с 3 верни и 0 случайни съвпадения
    получаваше „превес 3.00" и падаше под прага 4.0. Измерено 27.09.2026:
    `ARM_Bars` (s-9), `ARM.1` и `ARM.MR.2` (Тест-003), `Stremena` (тест-004),
    `Arm - N16` (тест-006) — ВСИЧКИТЕ с контрола 0 — падаха само заради това
    деление, а `Horiz.arm` (s-11) минаваше, защото случайно има точно 4.

    Затова прагът вече е по СЪЩЕСТВО: без нито едно случайно съвпадение се
    приема; иначе се иска превес поне ``MIN_CONTROL_RATIO``. Това не е
    отслабване — маха аритметичен похват, който нямаше инженерен смисъл.
    """

    if role != "REBAR_GEOMETRY":
        return True, ""
    контролни = _control_matches(counts)
    if контролни == 0:
        return True, "контролата е 0 — нито едно случайно съвпадение"
    превес = _support(counts, role) / контролни
    if превес >= MIN_CONTROL_RATIO:
        return True, "превес над контролата %.2f" % превес
    return False, ("превесът над контролата е %.2f при %d случайни съвпадения — "
                   "под %.1f не се приема" % (превес, контролни, MIN_CONTROL_RATIO))


def conflicting_name_role(
    layer_roles: Mapping[str, Any] | None, *, layer: str, role: str
) -> str:
    """Дали именният класификатор дава на ТОЗИ слой друга роля, и то уверено.

    Пази правилата „само по съдържание": те нямат ограничение по име, затова
    единствената спирачка срещу „познато име с неподходящо съдържание" е
    насрещното независимо мнение за самия слой.
    """

    from lion_rebar.learning.convention_learner import (
        NAME_CLASSIFIER_TRUST, _съвместими,
    )

    описание = (layer_roles or {}).get(layer) or {}
    чужда = str(описание.get("role") or "")
    увереност = float(описание.get("confidence") or 0.0)
    if (чужда and чужда != role and чужда != "UNKNOWN"
            and увереност >= NAME_CLASSIFIER_TRUST
            and not _съвместими(role, чужда)):
        return "%s (увереност %.2f)" % (чужда, увереност)
    return ""


def scope_refusal(
    правило: LearnedRule, *, target_style: Mapping[str, Any] | None
) -> str:
    """Защо правилото НЕ важи за този чертеж по ОБХВАТ. Празно значи важи.

    Обхватът е част от самото твърдение (``learning/scopes.py``): правило със
    СЕМЕЕН обхват важи само вътре в семейството на донорите си, а решение за
    позиция и проектна конвенция не важат за друг чертеж. Извън обхвата отговорът
    е отказ, не най-близкото.
    """

    from lion_rebar.learning import scopes

    в_обхват, обяснение = scopes.target_in_scope(правило, target_style=target_style)
    return "" if в_обхват else обяснение


def matching_rule(
    правила: Sequence[LearnedRule], *, role: str, layer: str,
    layer_roles: Mapping[str, Any] | None = None,
    target_style: Mapping[str, Any] | None = None,
) -> LearnedRule | None:
    """Приетото правило за тази роля и този слой.

    Първо се търси правило с признак на ИМЕТО — то е по-тясно и по-безопасно.
    Ако няма, се пробва правило „само по съдържание", но само когато именният
    класификатор не твърди уверено нещо друго за слоя.

    ``target_style`` е диалектът на целевия чертеж. Правило, чийто обхват е
    семеен, се пропуска, ако целевият чертеж не е доказано в същото семейство.
    """

    правило = _matching_rule_ignoring_scope(
        правила, role=role, layer=layer, layer_roles=layer_roles)
    if правило is None:
        return None
    if scope_refusal(правило, target_style=target_style):
        return None
    return правило


def _matching_rule_ignoring_scope(
    правила: Sequence[LearnedRule], *, role: str, layer: str,
    layer_roles: Mapping[str, Any] | None = None,
) -> LearnedRule | None:
    """Правилото, което ПАСВА по роля, име и режим — без проверка на обхвата.

    Разделено от ``matching_rule``, за да може отказът „има правило, но е извън
    обхвата" да се различи от „няма правило за това име". Двете причини водят до
    един и същ резултат, но не значат едно и също в отчета.
    """

    признак = name_token(layer)
    по_съдържание: LearnedRule | None = None
    for правило in правила:
        if правило.state != ACCEPTED or правило.role != role:
            continue
        режим = str(правило.signature.get("match_mode") or "NAME_TOKEN")
        if режим == "NAME_TOKEN":
            очакван = str(правило.signature.get("name_token") or "")
            if очакван and очакван == признак:
                return правило
            continue
        if правило.role in content_only_disabled_roles():
            # Паметта е постоянна: правило, прието от ПРЕДИШНА версия на
            # извличането, стои като ACCEPTED и иначе би се приложило след
            # обновяване на кода. Мерено на d-2: точно това сваля портата от
            # 124 на 105 от 178.
            continue
        if по_съдържание is None:
            по_съдържание = правило
    if по_съдържание is not None and not conflicting_name_role(
        layer_roles, layer=layer, role=role
    ):
        return по_съдържание
    return None


def apply_learned(
    профил: Mapping[str, Any],
    *,
    store: KnowledgeStore,
    drawing: str = "",
    enabled: bool = True,
    layer_roles: Mapping[str, Any] | None = None,
    target_style: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Разрешава откази на паспорта с наученото.

    ``enabled=False`` е режим Б: същият код, но наученото НЕ се ползва. Така
    разликата Б → В показва ефекта само от хранилището.
    """

    приети = store.accepted() if enabled else ()
    решени: list[dict[str, Any]] = []
    останали: list[dict[str, Any]] = []

    for отказ in профил.get("refusals") or ():
        роля = str(отказ.get("role") or "")
        слой = str(отказ.get("layer") or "")
        counts = отказ.get("counts") or {}
        своя_подкрепа = _support(counts, роля)

        правило = (matching_rule(приети, role=роля, layer=слой,
                                 layer_roles=layer_roles,
                                 target_style=target_style) if приети else None)
        причина_за_отказ = None
        причина_на_паспорта = str(отказ.get("reason") or "")

        if not count_shortage_refusal(причина_на_паспорта):
            причина_за_отказ = (
                "отказът НЕ е за малко примери, а по същество — наученото не го отменя"
            )
        elif правило is None:
            сблъсък = conflicting_name_role(layer_roles, layer=слой, role=роля)
            # Правило, спряно от ОБХВАТА, е различна причина от „няма правило" и
            # се казва отделно: така се вижда, че знание има, но не за това
            # семейство.
            пасващо = _matching_rule_ignoring_scope(
                приети, role=роля, layer=слой, layer_roles=layer_roles)
            по_обхват = ([scope_refusal(пасващо, target_style=target_style)]
                         if пасващо is not None
                         and scope_refusal(пасващо, target_style=target_style)
                         else [])
            причина_за_отказ = (
                ("правилото само по съдържание не се прилага: именният "
                 "класификатор дава %s за този слой" % сблъсък)
                if сблъсък else
                ("правило за %s има, но е извън обхвата: %s" % (роля, по_обхват[0]))
                if по_обхват else
                ("няма прието правило за %s с признак на име %s"
                 % (роля, name_token(слой) or "(празен)"))
            )
        elif своя_подкрепа < MIN_TARGET_SUPPORT:
            причина_за_отказ = (
                "целевият слой дава само %d свои примера — под %d наученото не се прилага"
                % (своя_подкрепа, MIN_TARGET_SUPPORT)
            )
        else:
            чисто, обяснение = control_is_clean(counts, роля)
            if not чисто:
                причина_за_отказ = обяснение

        if причина_за_отказ is not None:
            останали.append({
                "layer": слой, "role": роля,
                "own_support": своя_подкрепа,
                "profile_reason": str(отказ.get("reason") or ""),
                "learned_refusal": причина_за_отказ,
            })
            continue

        решени.append({
            "layer": слой,
            "role": роля,
            "own_support": своя_подкрепа,
            "control_ratio": _control_ratio(counts, роля),
            "profile_reason": str(отказ.get("reason") or ""),
            "rule_id": правило.rule_id,
            "rule_version": правило.version,
            "learned_from": list(правило.families),
            "learned_from_drawings": [str(п.get("drawing") or "")
                                      for п in правило.positives][:6],
            "learning_scope": (правило.scope or {}).get("learning_scope"),
            "independent_sources": (правило.scope or {}).get("independent_sources"),
            "signature": dict(правило.signature),
            "why": (
                "Съдържанието на този слой дава %d свои примера; правило %s "
                "(в.%d), доказано по съдържание в %s, приема от %d нагоре."
                % (своя_подкрепа, правило.rule_id, правило.version,
                   ", ".join(правило.families) or "—", MIN_TARGET_SUPPORT)
            ),
        })

    return {
        "schema": APPLY_SCHEMA,
        "applied_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "drawing": drawing,
        "learning_enabled": bool(enabled),
        "store_path": str(store.path),
        "accepted_rule_count": len(приети),
        "resolved": решени,
        "still_refused": останали,
        "summary": {
            "refusals_in": len(профил.get("refusals") or ()),
            "resolved_by_learning": len(решени),
            "still_refused": len(останали),
        },
    }


def authorized_proposals(
    профил: Mapping[str, Any],
    *,
    store: KnowledgeStore,
    enabled: bool = True,
    layer_roles: Mapping[str, Any] | None = None,
    target_style: Mapping[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Предложения на паспорта, които ВАЛИДИРАНО правило позволява да се ползват
    без операторско потвърждение.

    Разликата от ``apply_learned``: там наученото сваля прага за КОЛИЧЕСТВО при
    отказ. Тук отказ няма — съдържанието на чертежа само доказва ролята над
    прага. Липсва само разрешението да се действа, защото днес то идва от
    оператора през паспортния диалог (``ui/drawing_passport_dialog.py``).

    Наученото дава точно това разрешение: същата роля, с същия признак на име, е
    доказана по съдържание в поне две ДРУГИ семейства. Решението пак се опира на
    текущия чертеж — предложението е негово.
    """

    приети = store.accepted() if enabled else ()
    # Отказите се индексират по (слой, роля, БЛОК). Паспортът се произнася за
    # блоково семейство при ``POSITION_LABEL_BLOCK``, затова отказ за друг блок
    # на същия слой НЕ е противоречие — но се записва, вместо да се премълчава.
    # Измерено 27.09.2026: 4 случая (слой, роля) са едновременно разрешени и
    # отказани, всичките `ARMTXT` с два различни блока.
    откази_по_цел: dict[tuple[str, str, str], list[str]] = {}
    for отказ in профил.get("refusals") or ():
        ключ = (str(отказ.get("layer") or ""), str(отказ.get("role") or ""),
                str(отказ.get("block_name") or ""))
        откази_по_цел.setdefault(ключ, []).append(str(отказ.get("reason") or ""))

    изход: list[dict[str, Any]] = []
    for предложение in профил.get("proposals") or ():
        роля = str(предложение.get("role") or "")
        слой = str(предложение.get("layer") or "")
        блок = str(предложение.get("block_name") or "")
        if not слой:
            continue
        правило = matching_rule(приети, role=роля, layer=слой,
                                layer_roles=layer_roles,
                                target_style=target_style)
        if правило is None:
            continue

        # Отказ за СЪЩАТА цел (слой+роля+блок) е противоречие: не се разрешава.
        свои_откази = откази_по_цел.get((слой, роля, блок)) or []
        по_същество = [п for п in свои_откази if not count_shortage_refusal(п)]
        if по_същество:
            continue
        # Отказ за ДРУГ блок на същия слой — забележка, не спирачка.
        съседни = [
            {"block_name": ключ[2], "reason": причина[:110]}
            for ключ, причини in откази_по_цел.items()
            if ключ[0] == слой and ключ[1] == роля and ключ[2] != блок
            for причина in причини
        ]
        изход.append({
            "layer": слой,
            "role": роля,
            "block_name": блок,
            "own_evidence": str(предложение.get("evidence") or "")[:180],
            "own_counts": dict(предложение.get("counts") or {}),
            "rule_id": правило.rule_id,
            "rule_version": правило.version,
            "learned_from": list(правило.families),
            "learning_scope": (правило.scope or {}).get("learning_scope"),
            "independent_sources": (правило.scope or {}).get("independent_sources"),
            "match_mode": str(правило.signature.get("match_mode") or "NAME_TOKEN"),
            "refusals_on_other_targets": съседни,
            "why": (
                "Съдържанието на този чертеж доказва ролята само; правило %s "
                "(в.%d), валидирано в %s, разрешава да се ползва без питане."
                % (правило.rule_id, правило.version,
                   ", ".join(правило.families) or "—")
            ),
        })
    return изход


def merged_roles(
    профил: Mapping[str, Any], приложено: Mapping[str, Any]
) -> dict[str, dict[str, Any]]:
    """Ролите на слоевете след наученото — предложените плюс разрешените.

    Един слой може да получи роля от паспорта ИЛИ от наученото, никога и от
    двете: наученото гледа само откази.
    """

    изход: dict[str, dict[str, Any]] = {}
    for предложение in профил.get("proposals") or ():
        слой = str(предложение.get("layer") or "")
        if слой:
            изход[слой] = {
                "role": str(предложение.get("role") or ""),
                "source": "СЪДЪРЖАНИЕ (паспорт)",
                "evidence": str(предложение.get("evidence") or ""),
            }
    for решено in приложено.get("resolved") or ():
        слой = str(решено.get("layer") or "")
        if слой and слой not in изход:
            изход[слой] = {
                "role": str(решено.get("role") or ""),
                "source": "НАУЧЕНО (%s)" % решено.get("rule_id"),
                "evidence": str(решено.get("why") or ""),
            }
    return изход
