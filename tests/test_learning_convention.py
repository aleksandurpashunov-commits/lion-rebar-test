"""Научените правила за прочит: какво пазят тези тестове.

* хранилището преживява затваряне на процеса (записва се и се чете наново);
* правило се ПРИЕМА само при поне две различни семейства;
* контрапример го ОТХВЪРЛЯ;
* при прилагане нула или един свой пример в целевия чертеж е ОТКАЗ;
* отказ ПО СЪЩЕСТВО (неяснота) НЕ се отменя от наученото — това беше истински
  дефект в първата редакция, намерен при измерване на 27.09.2026;
* превесът над контролата (защитата срещу случайно съвпадение) НЕ се отслабва;
* признакът на името СТЕСНЯВА правилото — чужд слой не го получава;
* политиката се разширява, не се заменя, и без знание остава НЕПРОМЕНЕНА.
"""
from __future__ import annotations

import json

from lion_rebar.dxf.recognition_policy import DEFAULT_RECOGNITION_POLICY
from lion_rebar.learning.convention_apply import (
    apply_learned,
    authorized_proposals,
    conflicting_name_role,
    count_shortage_refusal,
    matching_rule,
)
from lion_rebar.learning.convention_learner import (
    MATCH_CONTENT_ONLY,
    MATCH_NAME_TOKEN,
    learn,
    name_token,
    signature_of,
)
from lion_rebar.learning.knowledge_store import (
    ACCEPTED,
    CANDIDATE,
    KNOWLEDGE_SCHEMA,
    REJECTED,
    KnowledgeStore,
    LearnedRule,
)
from lion_rebar.learning.policy_bridge import (
    policy_with_learned_knowledge,
    without_hardcoded_aliases,
)


def _предложение(слой: str, роля: str, **counts) -> dict:
    return {"target_kind": "LAYER", "layer": слой, "role": роля,
            "confidence": 0.9, "evidence": "проба", "sample_handles": ["H1"],
            "counts": dict(counts), "details": {}}


def _отказ(слой: str, роля: str, причина: str, **counts) -> dict:
    return {"target_kind": "LAYER", "layer": слой, "role": роля,
            "reason": причина, "counts": dict(counts)}


#: Общ диалект за наблюденията в този файл: 14 образеца, над ``МИН_ОБРАЗЦИ``.
#: Така двата донора са в ЕДНО стилово семейство и приетото правило има СЕМЕЕН
#: обхват — адаптация в семейството, не пренос между семейства.
_ДИАЛЕКТ = tuple("образец %02d #N#" % и for и in range(1, 15))


def _подпис(семейство: str) -> dict:
    """Подпис по съдържание: РАЗЛИЧНИ надписи, ОБЩ диалект.

    Различните надписи правят източниците независими (покритие 0), а общият
    диалект ги слага в едно семейство. Точно това е случаят „два независими
    проекта в едно семейство" от § 5 на заданието.
    """

    from lion_rebar.learning.source_identity import signature_from_labels

    надписи = ["%s надпис %02d" % (семейство, и) for и in range(1, 21)]
    return signature_from_labels(надписи, patterns=_ДИАЛЕКТ)


def _целеви_стил() -> dict:
    """Диалектът на ЦЕЛЕВИЯ чертеж — същото семейство като донорите."""

    from lion_rebar.learning.source_identity import style_signature

    return style_signature(_подпис("цел"))


def _наблюдение(чертеж: str, семейство: str, предложения, *, роли=None,
                locked=False) -> dict:
    return {
        "drawing": чертеж, "family": семейство, "locked": locked,
        "profile": {"proposer_version": "1.0", "proposals": предложения,
                    "refusals": []},
        "layer_roles": роли or {},
        # Наблюдението носи своя НЕЗАВИСИМ ИЗТОЧНИК и подписа си по съдържание —
        # откакто портата брои източници, а не имена (30.09.2026).
        "independence_group": семейство,
        "content_signature": _подпис(семейство),
    }


# --- хранилището ------------------------------------------------------------


def test_hranilishteto_prezhivyava_restart(tmp_path) -> None:
    """Записано правило се чете наново от ДРУГ обект — както след рестарт."""

    път = tmp_path / "naucheno.json"
    store = KnowledgeStore.load(път)
    store.put(LearnedRule(
        rule_id="CR-TEST-1", version=1, role="ZONE_TITLE",
        signature=signature_of("ZONE_TITLE", "Layer6", {"zone_titles": 4}),
        state=ACCEPTED, origin="тест",
        positives=[{"drawing": "а", "family": "А", "support": 4},
                   {"drawing": "б", "family": "Б", "support": 5}],
    ))
    store.save()

    наново = KnowledgeStore.load(път)
    assert len(наново) == 1
    правило = наново.get("CR-TEST-1")
    assert правило is not None
    assert правило.state == ACCEPTED
    assert правило.families == ("А", "Б")
    записано = json.loads(път.read_text(encoding="utf-8"))
    assert записано["schema"] == KNOWLEDGE_SCHEMA


def test_versiyata_raste_pri_promyana(tmp_path) -> None:
    store = KnowledgeStore.load(tmp_path / "naucheno.json")
    основа = LearnedRule(
        rule_id="CR-TEST-2", version=1, role="ZONE_TITLE",
        signature={"role": "ZONE_TITLE"}, state=CANDIDATE, origin="тест",
    )
    първо = store.put(основа)
    второ = store.put(LearnedRule(**{**основа.to_dict(), "state": ACCEPTED}))
    assert (първо.version, второ.version) == (1, 2)
    assert store.withdraw("CR-TEST-2", replaced_by="CR-TEST-3").version == 3


# --- обучението ------------------------------------------------------------


def test_priema_se_samo_pri_dve_razlichni_semeystva(tmp_path) -> None:
    """Правилото ПО ИМЕ иска две семейства. Всяко предложение храни и двата
    режима, затова тук се гледат само правилата по име."""

    store = KnowledgeStore.load(tmp_path / "naucheno.json")
    отчет = learn([
        _наблюдение("а", "А", [_предложение("Layer1", "ZONE_TITLE", zone_titles=4)]),
        _наблюдение("б", "Б", [_предложение("Layer2", "ZONE_TITLE", zone_titles=6)]),
        _наблюдение("в", "В", [_предложение("Section", "ZONE_TITLE", zone_titles=3)]),
    ], store=store)

    def по_име(редове):
        return {р["name_token"] for р in редове
                if store.get(р["rule_id"]).signature.get("match_mode") == MATCH_NAME_TOKEN}

    assert по_име(отчет["accepted"]) == {"LAYER"}    # Layer1 + Layer2 -> две семейства
    assert по_име(отчет["candidates"]) == {"SECTION"}  # само едно семейство


def test_edno_semeystvo_ostava_kandidat(tmp_path) -> None:
    """Две копия на ЕДИН чертеж не са две доказателства."""

    store = KnowledgeStore.load(tmp_path / "naucheno.json")
    отчет = learn([
        _наблюдение("а", "А", [_предложение("Layer1", "ZONE_TITLE", zone_titles=4)]),
        _наблюдение("а-копие", "А", [_предложение("Layer2", "ZONE_TITLE", zone_titles=4)]),
    ], store=store)
    assert отчет["accepted"] == []
    # по едно кандидат-правило за всеки режим; и двата остават кандидати
    режими = {store.get(р["rule_id"]).signature.get("match_mode")
              for р in отчет["candidates"]}
    assert режими == {MATCH_NAME_TOKEN, MATCH_CONTENT_ONLY}


def test_kontraprimer_othvurlya_praviloto(tmp_path) -> None:
    """Именният класификатор с увереност >= 0.96 е независимо доказателство."""

    store = KnowledgeStore.load(tmp_path / "naucheno.json")
    отчет = learn([
        _наблюдение("а", "А", [_предложение("Ramka", "ZONE_TITLE", zone_titles=4)],
                    роли={"Ramka": {"role": "FRAME", "confidence": 0.99}}),
        _наблюдение("б", "Б", [_предложение("Ramka", "ZONE_TITLE", zone_titles=4)]),
    ], store=store)
    assert отчет["accepted"] == []
    # контрапримерът отхвърля и двата режима — иначе правилото по съдържание би
    # преживяло насрещното доказателство
    отхвърлени = store.by_state(REJECTED)
    assert {п.signature.get("match_mode") for п in отхвърлени} == {
        MATCH_NAME_TOKEN, MATCH_CONTENT_ONLY}
    for правило in отхвърлени:
        assert "FRAME" in правило.counterexamples[0]["reason"]


def test_zaklyucheno_semeystvo_ne_uchi(tmp_path) -> None:
    store = KnowledgeStore.load(tmp_path / "naucheno.json")
    отчет = learn([
        _наблюдение("а", "А", [_предложение("Layer1", "ZONE_TITLE", zone_titles=4)]),
        _наблюдение("з", "З", [_предложение("Layer2", "ZONE_TITLE", zone_titles=4)],
                    locked=True),
    ], store=store)
    assert отчет["accepted"] == []
    assert отчет["excluded_locked"] == ["з"]


# --- прилагането -----------------------------------------------------------


def _хранилище_с_прието(tmp_path, роля="ZONE_TITLE", слой="Layer1") -> KnowledgeStore:
    store = KnowledgeStore.load(tmp_path / "naucheno.json")
    learn([
        _наблюдение("а", "А", [_предложение(слой, роля, zone_titles=4,
                                            local_length_matches=9,
                                            control_matches=1,
                                            dominant_tag_set=9, inside_circle=9)]),
        _наблюдение("б", "Б", [_предложение(слой, роля, zone_titles=6,
                                            local_length_matches=9,
                                            control_matches=1,
                                            dominant_tag_set=9, inside_circle=9)]),
    ], store=store)
    return store


def test_nula_svoi_primeri_e_otkaz(tmp_path) -> None:
    store = _хранилище_с_прието(tmp_path)
    профил = {"proposals": [], "refusals": [
        _отказ("Layer6", "ZONE_TITLE", "само 1 надписа изглеждат като заглавие — под 3 не се предлага",
               zone_titles=1)]}
    изход = apply_learned(профил, store=store, target_style=_целеви_стил())
    assert изход["resolved"] == []
    assert "само 1 свои примера" in изход["still_refused"][0]["learned_refusal"]


def test_dva_svoi_primera_se_priemat(tmp_path) -> None:
    store = _хранилище_с_прието(tmp_path)
    профил = {"proposals": [], "refusals": [
        _отказ("Layer6", "ZONE_TITLE", "само 2 надписа изглеждат като заглавие — под 3 не се предлага",
               zone_titles=2)]}
    изход = apply_learned(профил, store=store, target_style=_целеви_стил())
    assert len(изход["resolved"]) == 1
    assert изход["resolved"][0]["learned_from"] == ["А", "Б"]


def test_otkaz_po_sushtestvo_ne_se_otmenya(tmp_path) -> None:
    """Истинският дефект от 27.09: неяснота не е „малко примери"."""

    store = _хранилище_с_прието(tmp_path, роля="POSITION_LABEL_BLOCK")
    профил = {"proposals": [], "refusals": [
        _отказ("Layer1", "POSITION_LABEL_BLOCK",
               "два или повече тага носят само стандартни диаметри",
               dominant_tag_set=22)]}
    изход = apply_learned(профил, store=store, target_style=_целеви_стил())
    assert изход["resolved"] == []
    assert "по същество" in изход["still_refused"][0]["learned_refusal"]


def test_prevesut_nad_kontrolata_ne_se_otslabva(tmp_path) -> None:
    """Защитата срещу случайно съвпадение важи и при научено правило."""

    store = _хранилище_с_прието(tmp_path, роля="REBAR_GEOMETRY")
    профил = {"proposals": [], "refusals": [
        _отказ("Layer1", "REBAR_GEOMETRY",
               "само 3 надписа намират дължината си тук — под 5",
               local_length_matches=3, control_matches=1)]}
    изход = apply_learned(профил, store=store, target_style=_целеви_стил())
    assert изход["resolved"] == []
    assert "превесът над контролата" in изход["still_refused"][0]["learned_refusal"]


def test_priznakut_na_imeto_stesnyava(tmp_path) -> None:
    """Слой с друго име не получава правилото, колкото и примери да има."""

    store = _хранилище_с_прието(tmp_path)
    профил = {"proposals": [], "refusals": [
        _отказ("СъвсемДругСлой", "ZONE_TITLE",
               "само 2 надписа изглеждат като заглавие — под 3 не се предлага",
               zone_titles=2)]}
    изход = apply_learned(профил, store=store, target_style=_целеви_стил())
    assert изход["resolved"] == []
    assert "признак на име" in изход["still_refused"][0]["learned_refusal"]


def test_count_shortage_razpoznava_vidut_na_otkaza() -> None:
    assert count_shortage_refusal("само 2 надписа — под 3 не се предлага")
    assert not count_shortage_refusal("два или повече тага носят само стандартни диаметри")
    assert not count_shortage_refusal("нито един от таговете ОС не носи само стандартни")
    assert not count_shortage_refusal("само 8 надписа, а контролата дава 3")


# --- политиката ------------------------------------------------------------


def test_bez_znanie_politikata_e_nepromenena(tmp_path) -> None:
    """Режим Б трябва да е ТОЧНО като режим А."""

    празно = KnowledgeStore.load(tmp_path / "naucheno.json")
    политика, отчет = policy_with_learned_knowledge(
        DEFAULT_RECOGNITION_POLICY, [], store=празно,
    )
    assert политика is DEFAULT_RECOGNITION_POLICY
    assert отчет["policy_changed"] is False


def test_izklyucheno_ne_polzva_hranilishteto(tmp_path) -> None:
    store = _хранилище_с_прието(tmp_path)
    политика, отчет = policy_with_learned_knowledge(
        DEFAULT_RECOGNITION_POLICY, [], store=store, enabled=False,
    )
    assert политика is DEFAULT_RECOGNITION_POLICY
    assert отчет["learning_enabled"] is False


def test_razresheno_predlozhenie_pulni_politikata(tmp_path) -> None:
    """Чертежът сам доказва ролята; наученото дава правото да се ползва."""

    store = _хранилище_с_прието(tmp_path, роля="POSITION_KEY", слой="0.4")
    профил = {"proposals": [_предложение("0.4", "POSITION_KEY", inside_circle=106)],
              "refusals": []}
    разрешени = authorized_proposals(профил, store=store,
                                     target_style=_целеви_стил())
    assert len(разрешени) == 1
    assert разрешени[0]["learned_from"] == ["А", "Б"]


def test_bez_izbroenite_v_koda_politikata_e_prazna() -> None:
    гола = without_hardcoded_aliases(DEFAULT_RECOGNITION_POLICY)
    assert DEFAULT_RECOGNITION_POLICY.position_key_circle_layer_aliases == ("0.4",)
    assert гола.position_key_circle_layer_aliases == ()
    assert гола.position_key_table_layer_aliases == ()


def test_imeto_na_sloya_dava_priznak() -> None:
    assert name_token("Layer6") == name_token("Layer1") == "LAYER"
    assert name_token("Arm - N12") == name_token("Arm - N14") == "ARM|N"
    assert name_token("ARM_TXT") == "ARM|TXT"
    assert name_token("ARMTXT") == "ARMTXT"


# --- режим „само по съдържание" (етап 3) -----------------------------------


def _наблюдение_сл(чертеж, семейство, слой, роля="ZONE_TITLE", **counts):
    return _наблюдение(чертеж, семейство,
                       [_предложение(слой, роля, **(counts or {"zone_titles": 4}))])


def test_samo_po_sudurzhanie_iska_tri_semeystva(tmp_path) -> None:
    """Две семейства стигат за правило по ИМЕ, но не и за правило по съдържание."""

    store = KnowledgeStore.load(tmp_path / "naucheno.json")
    отчет = learn([
        _наблюдение_сл("а", "А", "Alpha"),
        _наблюдение_сл("б", "Б", "Beta"),
    ], store=store)
    режими = {р["rule_id"]: store.get(р["rule_id"]).signature.get("match_mode")
              for р in отчет["accepted"]}
    assert set(режими.values()) == {MATCH_NAME_TOKEN} or not режими
    съдържание = [р for р in отчет["candidates"]
                  if store.get(р["rule_id"]).signature.get("match_mode") == MATCH_CONTENT_ONLY]
    assert съдържание, "правилото по съдържание трябва да остане кандидат при 2 семейства"


def test_samo_po_sudurzhanie_iska_i_razlichni_imena(tmp_path) -> None:
    """Три семейства с ЕДНО И СЪЩО име не доказват независимост от името."""

    store = KnowledgeStore.load(tmp_path / "naucheno.json")
    отчет = learn([
        _наблюдение_сл("а", "А", "Layer1"),
        _наблюдение_сл("б", "Б", "Layer2"),
        _наблюдение_сл("в", "В", "Layer6"),
    ], store=store)
    по_съдържание = [
        р for р in отчет["accepted"] + отчет["candidates"]
        if store.get(р["rule_id"]).signature.get("match_mode") == MATCH_CONTENT_ONLY
    ]
    assert len(по_съдържание) == 1
    правило = store.get(по_съдържание[0]["rule_id"])
    # Layer1/2/6 дават един и същ признак -> маскирано правило по име
    assert правило.state == CANDIDATE
    assert "маскирано" in правило.validation["verdict"]


def test_samo_po_sudurzhanie_se_priema_pri_razlichni_imena(tmp_path) -> None:
    store = KnowledgeStore.load(tmp_path / "naucheno.json")
    отчет = learn([
        _наблюдение_сл("а", "А", "Alpha"),
        _наблюдение_сл("б", "Б", "Beta"),
        _наблюдение_сл("в", "В", "Gamma"),
    ], store=store)
    приети = [store.get(р["rule_id"]) for р in отчет["accepted"]]
    режими = {п.signature.get("match_mode") for п in приети}
    assert MATCH_CONTENT_ONLY in режими


def test_praviloto_po_sudurzhanie_vazhi_za_preimenuvan_sloy(tmp_path) -> None:
    """Смисълът на режима: непознато име със същото съдържание получава правилото."""

    store = KnowledgeStore.load(tmp_path / "naucheno.json")
    learn([
        _наблюдение_сл("а", "А", "Alpha"),
        _наблюдение_сл("б", "Б", "Beta"),
        _наблюдение_сл("в", "В", "Gamma"),
    ], store=store)
    профил = {"proposals": [], "refusals": [
        _отказ("СъвсемНовоИме", "ZONE_TITLE",
               "само 2 надписа изглеждат като заглавие — под 3 не се предлага",
               zone_titles=2)]}
    изход = apply_learned(профил, store=store, target_style=_целеви_стил())
    assert len(изход["resolved"]) == 1
    assert изход["resolved"][0]["layer"] == "СъвсемНовоИме"


def test_imenniyat_klasifikator_spira_praviloto_po_sudurzhanie(tmp_path) -> None:
    """Познато име с неподходящо съдържание: насрещното мнение има думата."""

    store = KnowledgeStore.load(tmp_path / "naucheno.json")
    learn([
        _наблюдение_сл("а", "А", "Alpha"),
        _наблюдение_сл("б", "Б", "Beta"),
        _наблюдение_сл("в", "В", "Gamma"),
    ], store=store)
    профил = {"proposals": [], "refusals": [
        _отказ("Ramka", "ZONE_TITLE",
               "само 2 надписа изглеждат като заглавие — под 3 не се предлага",
               zone_titles=2)]}
    роли = {"Ramka": {"role": "FRAME", "confidence": 0.99}}
    изход = apply_learned(профил, store=store, layer_roles=роли,
                          target_style=_целеви_стил())
    assert изход["resolved"] == []
    assert "FRAME" in изход["still_refused"][0]["learned_refusal"]


def test_conflicting_name_role_mulchi_pri_saglasie() -> None:
    роли = {"L": {"role": "REBAR_TEXT", "confidence": 0.99}}
    # POSITION_ANNOTATION и REBAR_TEXT са едно и също нещо с различни имена
    assert conflicting_name_role(роли, layer="L", role="POSITION_ANNOTATION") == ""
    assert conflicting_name_role(роли, layer="L", role="ZONE_TITLE") != ""
    assert conflicting_name_role({}, layer="L", role="ZONE_TITLE") == ""


def test_razreshenieto_e_po_blok_a_ne_po_sloy(tmp_path) -> None:
    """Отказ за СЪЩИЯ блок спира; отказ за друг блок е само забележка.

    Измерено 27.09.2026: `ARMTXT` в d-1/s-10/s-11/тест-004 носи блок с
    еднозначни тагове (предложение) и друг блок с двусмислени (отказ).
    """

    store = _хранилище_с_прието(tmp_path, роля="POSITION_LABEL_BLOCK", слой="ARMTXT")
    предложение = _предложение("ARMTXT", "POSITION_LABEL_BLOCK", dominant_tag_set=9)
    предложение["block_name"] = "ARM_NX"
    свой_отказ = _отказ("ARMTXT", "POSITION_LABEL_BLOCK",
                        "два или повече тага носят само стандартни диаметри",
                        dominant_tag_set=9)
    свой_отказ["block_name"] = "ARM_NX"
    чужд_отказ = dict(свой_отказ, block_name="ARM_DRUG")

    # отказ за СЪЩИЯ блок -> не се разрешава
    изход = authorized_proposals({"proposals": [предложение], "refusals": [свой_отказ]},
                                 store=store, target_style=_целеви_стил())
    assert изход == []

    # отказ за ДРУГ блок -> разрешава се, но забележката се записва
    изход = authorized_proposals({"proposals": [предложение], "refusals": [чужд_отказ]},
                                 store=store, target_style=_целеви_стил())
    assert len(изход) == 1
    assert изход[0]["refusals_on_other_targets"][0]["block_name"] == "ARM_DRUG"
