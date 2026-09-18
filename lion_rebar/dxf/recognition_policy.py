from __future__ import annotations

from dataclasses import asdict, dataclass, fields
from typing import Any, Mapping


@dataclass(frozen=True)
class RecognitionPolicy:
    """Auditable laws for geometric candidate search and shape matching.

    These values are safety gates, not automatic approval rules. They can be
    overridden from ``EnginePackage.settings['geometry_recognition']`` through
    MASTER Studio. Missing or invalid values fall back to these defaults.
    """

    endpoint_tolerance_raw: float = 0.75
    # Conservative review-only recovery for drafting gaps.  The strict
    # endpoint graph above remains the source of truth.  This fallback may join
    # exactly two unbranched components through one unique endpoint gap, then
    # only when the complete result matches a MASTER shape and the annotated
    # production length within the exact tolerance.
    near_gap_healing_enabled: bool = True
    near_gap_max_raw: float = 2.0
    near_gap_require_master_match: bool = True
    near_gap_require_exact_length: bool = True
    # Lower than the schematic-scale penalty: an exact-production full chain
    # with one 1–2 unit drafting gap is stronger evidence than an unrelated
    # uniformly scaled symbol, while still being forced to manual review.
    near_gap_score_penalty: float = 0.25
    max_component_segments: int = 16
    max_path_candidates: int = 300

    min_search_distance_raw: float = 55.0
    max_search_distance_raw: float = 90.0
    search_distance_ratio: float = 0.25
    # A second, deliberately review-only search ring catches labels placed
    # farther from their schematic bar.  Geometry found only in this ring can
    # never become a strong candidate.
    extended_search_distance_raw: float = 240.0
    extended_search_distance_ratio: float = 0.60

    max_length_difference_abs: float = 25.0
    max_length_difference_ratio: float = 0.20

    exact_length_abs: float = 1.0
    exact_length_ratio: float = 0.005
    # A straight bar has no bend allocation or hidden hook length.  A narrowly
    # calibrated proportional tolerance may therefore be used only after the
    # geometry has an unambiguous configured straight-bar MASTER identity.
    # The global exact tolerance above remains unchanged for every other form.
    straight_bar_exact_length_rule_enabled: bool = False
    straight_bar_exact_length_master_codes: tuple[str, ...] = ("F001",)
    straight_bar_exact_length_ratio: float = 0.005
    straight_bar_exact_length_max_abs_cm: float = 5.0
    review_length_abs: float = 12.0
    review_length_ratio: float = 0.05

    strong_distance_abs: float = 45.0
    strong_distance_ratio: float = 0.12
    review_distance_abs: float = 60.0
    review_distance_ratio: float = 0.25

    # v0.1.17: confidence is based on measurable separation from the next
    # candidate instead of one fixed score gap that was too strict for repeated
    # parallel bars. These gates affect ranking labels only; they never create
    # automatic approvals.
    strong_score_gap: float = 0.04
    strong_distance_advantage_raw: float = 8.0
    generic_layer_strong_enabled: bool = True
    generic_layer_strong_score_gap: float = 0.10
    generic_layer_strong_distance_advantage_raw: float = 15.0

    # A same-band global ownership conflict is resolved only when three
    # independent numeric signals agree: production-length fit, annotation
    # distance and complete candidate score.  This is intentionally stricter
    # than ordinary candidate ranking because the losing annotation is denied
    # ownership of the shared DXF geometry.
    ownership_tiebreak_min_length_advantage_cm: float = 2.0
    ownership_tiebreak_min_distance_advantage_raw: float = 8.0
    ownership_tiebreak_min_score_advantage: float = 0.04

    collinear_merge_tolerance_deg: float = 2.0
    angle_match_tolerance_deg: float = 8.0
    length_ratio_tolerance: float = 0.12
    master_ambiguity_score_gap: float = 0.08

    # Uniformly scaled schematic geometry is accepted as a candidate only
    # when its complete chain matches a tested MASTER profile.  It is always
    # routed to manual review and never treated as measured production length.
    schematic_scale_min: float = 0.20
    schematic_scale_max: float = 5.00
    schematic_scale_exact_tolerance: float = 0.03
    # A scaled MASTER profile is useful evidence, but it must not outrank a
    # closer full chain whose measured DXF length agrees with the annotation.
    schematic_scale_score_penalty: float = 0.40

    prefer_maximal_master_chain: bool = True
    subset_chain_score_penalty: float = 0.35

    reject_explicit_diameter_mismatch: bool = True
    reject_branched_candidate: bool = True
    allow_reversed_direction: bool = True
    rotation_invariant: bool = True
    mirror_requires_shape_permission: bool = True

    # Explicit, configurable aliases for layers that carry curved rebar
    # centrelines but do not contain the generic ARM/REBAR tokens.  These are
    # semantic prefixes from real reinforcement drawings, not a broad
    # substring whitelist.  Geometry admitted through this path remains
    # review-only unless it has an exact, unique MASTER match.
    curved_centerline_layer_aliases: tuple[str, ...] = (
        "STREM",
        "СТРЕМ",
        "ESOV",
        "ЕСОВ",
    )

    # Owner-approved resolver for the designer's C/S punching-rebar symbols.
    # In the DXF the ESove centreline has one tangent bend and therefore
    # normalizes to two visible straight parts.  The production F007 MASTER,
    # however, is L1 + L2 + L1 (for example 10 + 20 + 10 = 40 cm).  The
    # resolver is deliberately restricted by layer, curve count, turn, ratio
    # and total length before it may restore the three production segments.
    c_shape_curve_rule_enabled: bool = True
    c_shape_curve_turn_deg: float = 135.0
    c_shape_curve_turn_tolerance_deg: float = 8.0
    c_shape_curve_long_to_short_ratio: float = 2.0
    c_shape_curve_ratio_tolerance: float = 0.25
    c_shape_curve_total_tolerance_cm: float = 2.5
    # Candidate admission for the same fully proven ESove/F007 resolver.  It
    # does not alter the generic search radius and a remote curve is discarded
    # unless every F007 topology/ratio/turn/length gate succeeds.
    c_shape_curve_search_distance_raw: float = 120.0
    # The complete F007 resolver above already proves layer, curve count,
    # turn, visible-arm ratio, total cut length and one unique MASTER code.
    # It may therefore use a narrowly extended association distance without
    # changing the generic strong-distance gate for any other form.
    c_shape_curve_strong_distance_raw: float = 60.0

    # Length-first resolver for repeated curved stirrup centrelines.  The
    # generic association radii remain unchanged.  This path admits only
    # exact configured layers, exact physical arc length, one configured
    # MASTER family and either one unique geometry or one complete repeated
    # row whose member count is compatible with the annotation count.
    stirrup_length_group_rule_enabled: bool = True
    stirrup_length_group_layer_aliases: tuple[str, ...] = (
        "STREMENA",
        "СТРЕМЕНА",
    )
    stirrup_length_group_master_codes: tuple[str, ...] = ("F006",)
    stirrup_length_search_distance_raw: float = 205.0
    stirrup_arc_length_tolerance_cm: float = 2.0
    stirrup_group_min_members: int = 2
    stirrup_group_max_member_spacing_raw: float = 80.0
    stirrup_group_max_row_deviation_raw: float = 5.0
    stirrup_group_repeat_length_tolerance_cm: float = 0.5
    stirrup_group_require_count_compatibility: bool = True

    # A close local tie may be resolved only when the leading candidate has a
    # complete A-E production-dimension equation and the runner-up has no
    # dimension evidence.  Both candidates must otherwise describe the same
    # unambiguous MASTER family and the leader must already be better by score
    # and distance.  This changes confidence only; it creates no approval.
    dimension_candidate_tiebreak_enabled: bool = True
    dimension_candidate_tiebreak_total_tolerance_cm: float = 0.51

    # A free alternative selected after global ownership allocation normally
    # stays manual.  The sole strong exception requires an exact A-E equation,
    # exact length, unique MASTER, explicit diameter-layer agreement and no
    # residual ownership overlap.
    verified_alternative_recovery_enabled: bool = True
    verified_alternative_dimension_tolerance_cm: float = 0.51

    # A table annotation may reference a remote legend/detail through a
    # repeated position key (for example 4a).  This resolver is intentionally
    # narrower than general proximity search: exact entity types, layers,
    # block families, one legend spec, one table circle, exact Ø/L and one
    # strong unambiguous MASTER geometry are all mandatory.
    position_key_legend_rule_enabled: bool = True
    # A proven legend reference is stronger than a weak local candidate.  This
    # switch extends the same resolver to MANUAL_REVIEW and RISK rows (RISK
    # since 2026-09-17, owner decision on s-11); strong candidates remain
    # outside its scope.
    position_key_manual_review_override_enabled: bool = False
    position_key_circle_layer_aliases: tuple[str, ...] = ("0.4",)
    position_key_spec_layer_aliases: tuple[str, ...] = ("ARMTXT1",)
    position_key_table_layer_aliases: tuple[str, ...] = ("ARMTXT", "ARM_TXT")
    position_key_table_block_aliases: tuple[str, ...] = (
        "ARM_N",
        "ARM_NX",
        "ARM_NXX",
    )
    position_key_legend_radius_raw: float = 60.0
    position_key_table_radius_raw: float = 110.0
    position_key_legend_geometry_max_distance_raw: float = 15.0
    position_key_legend_length_tolerance_cm: float = 1.0
    position_key_value_tolerance: float = 0.01

    # F003 and F005 can have the same three-segment U topology.  They are
    # distinct production subtypes and geometry alone must not silently merge
    # them.  A named context row (for example ``фиби 32N8/s=20``) may resolve
    # the subtype only when count + diameter identify one unique U signature
    # in the same batch.  Otherwise the MASTER ambiguity remains visible.
    named_u_subtype_context_rule_enabled: bool = True

    # Local beam-detail labels such as ``%%Uфиби.:`` and ``%%Uстр.:`` are
    # stronger evidence than a generic nearby U-shape.  They are paired
    # one-to-one with the adjacent full ARM_N annotation before matching.
    # Numeric dimension texts are also partitioned locally so one detail cannot
    # silently borrow another detail's printed dimensions.
    local_detail_context_rule_enabled: bool = True
    local_detail_label_max_distance_raw: float = 95.0
    local_detail_label_ambiguity_margin_raw: float = 8.0
    local_detail_dimension_max_distance_raw: float = 165.0
    local_detail_dimension_min_margin_raw: float = 8.0

    # Rounded F006 symbols in revised beam drawings consist of four straight
    # cage sides, four tangent quarter-arcs and two separate parallel keys.
    # This strict topology is distinct from the older six-LINE symbol and is
    # searched only when the local detail explicitly says ``стр.``/``стреме``
    # or when ordinary matching produced no candidate.
    rounded_f006_composite_rule_enabled: bool = True
    rounded_f006_search_distance_raw: float = 185.0
    rounded_f006_arc_sweep_tolerance_deg: float = 8.0
    rounded_f006_parallel_tolerance_deg: float = 8.0
    rounded_f006_radius_relative_tolerance: float = 0.20

    # Owner-approved proportion rule (recovery.2, 2026-08-06) for a U pair the
    # ENGINE itself declares mutually distinct (``recognition.distinct_from``)
    # and partitions with an explicit two-sided
    # ``recognition.u_outer_arms_relation``:
    # * BOTH outer arms SHORTER than the middle -> F003
    #   (прът с два винкела, e.g. 25-400-25);
    # * BOTH outer arms LONGER than the middle -> F005
    #   (фиба, including asymmetric examples such as 90-20-50);
    # * mixed arms or either arm within the tolerance band -> the conflict
    #   stays visible for manual review.
    # The rule consumes only ENGINE declarations; engines without them (for
    # example v0.3.6) keep the plain conflict.
    u_subtype_proportion_rule_enabled: bool = True
    u_subtype_arm_ratio_tolerance: float = 0.10

    # Annotation laws introduced in v0.1.14.
    include_text_mtext_annotations: bool = True
    use_visual_text_extent: bool = True
    use_attached_leader_points: bool = True
    leader_attach_distance_raw: float = 8.0
    leader_attach_height_ratio: float = 4.0

    # Project-approved rule for the designer's beam middle N8 bars.
    # It applies only to a two-segment ARM_Bars chain where the long segment
    # equals L - 15 cm within tolerance and the second visual arm is short.
    beam_n8_middle_rule_enabled: bool = True
    beam_n8_hook_length_cm: float = 15.0
    beam_n8_long_segment_tolerance_cm: float = 2.0
    beam_n8_short_segment_max_ratio: float = 0.20
    beam_n8_min_raw_turn_deg: float = 10.0
    beam_n8_max_raw_turn_deg: float = 170.0

    @classmethod
    def from_settings(cls, settings: Mapping[str, Any] | None) -> "RecognitionPolicy":
        raw = settings or {}
        if "geometry_recognition" in raw and isinstance(raw.get("geometry_recognition"), Mapping):
            raw = raw["geometry_recognition"]  # type: ignore[index]
        defaults = cls()
        values: dict[str, Any] = {}
        for item in fields(cls):
            default = getattr(defaults, item.name)
            value = raw.get(item.name, default) if isinstance(raw, Mapping) else default
            try:
                if isinstance(default, bool):
                    if isinstance(value, str):
                        value = value.strip().upper() in {"1", "TRUE", "YES", "ДА", "Y"}
                    else:
                        value = bool(value)
                elif isinstance(default, int):
                    value = max(1, int(value))
                elif isinstance(default, tuple):
                    if isinstance(value, str):
                        value = tuple(
                            token.strip()
                            for token in value.replace(";", ",").split(",")
                            if token.strip()
                        )
                    elif isinstance(value, (list, tuple, set)):
                        value = tuple(
                            str(token).strip()
                            for token in value
                            if str(token).strip()
                        )
                    else:
                        value = default
                else:
                    value = float(value)
            except (TypeError, ValueError):
                value = default
            values[item.name] = value
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def primary_search_distance_limit(self, declared_length_cm: float) -> float:
        dynamic = max(self.min_search_distance_raw, declared_length_cm * self.search_distance_ratio)
        return min(self.max_search_distance_raw, dynamic)

    def search_distance_limit(self, declared_length_cm: float) -> float:
        primary = self.primary_search_distance_limit(declared_length_cm)
        extended = max(
            primary,
            declared_length_cm * self.extended_search_distance_ratio,
        )
        return min(self.extended_search_distance_raw, extended)

    def hard_length_difference_limit(self, declared_length_cm: float) -> float:
        return max(self.max_length_difference_abs, declared_length_cm * self.max_length_difference_ratio)

    def exact_length_tolerance(self, declared_length_cm: float) -> float:
        return max(self.exact_length_abs, declared_length_cm * self.exact_length_ratio)

    def exact_length_tolerance_for_master(
        self,
        declared_length_cm: float,
        master_shape_code: str | None,
    ) -> float:
        """Return the exact-L gate after an unambiguous MASTER is known.

        The F001-specific branch can only widen the global gate.  It is bounded
        by an explicit absolute ceiling so a very long bar cannot acquire an
        unbounded proportional allowance.
        """

        base = self.exact_length_tolerance(declared_length_cm)
        if not self.straight_bar_exact_length_rule_enabled:
            return base
        code = str(master_shape_code or "").strip().upper()
        allowed = {
            str(value).strip().upper()
            for value in self.straight_bar_exact_length_master_codes
            if str(value).strip()
        }
        if code not in allowed:
            return base
        widened = min(
            max(0.0, float(self.straight_bar_exact_length_max_abs_cm)),
            max(0.0, float(declared_length_cm))
            * max(0.0, float(self.straight_bar_exact_length_ratio)),
        )
        return max(base, widened)

    def review_length_tolerance(self, declared_length_cm: float) -> float:
        return max(self.review_length_abs, declared_length_cm * self.review_length_ratio)

    def strong_distance_limit(self, declared_length_cm: float) -> float:
        return min(
            self.primary_search_distance_limit(declared_length_cm),
            max(self.strong_distance_abs, declared_length_cm * self.strong_distance_ratio),
        )

    def review_distance_limit(self, declared_length_cm: float) -> float:
        return min(
            self.primary_search_distance_limit(declared_length_cm),
            max(self.review_distance_abs, declared_length_cm * self.review_distance_ratio),
        )

    def laws_bg(self) -> tuple[str, ...]:
        return (
            "Закон 1 — използва се само геометрия от армировъчни слоеве.",
            "Закон 2 — изрично несъвпадение на диаметъра в слоя се отхвърля.",
            "Закон 3 — отделни сегменти се свързват само през общи крайни точки.",
            "Закон 4 — разклонена геометрия не се приема като една армировъчна форма.",
            "Закон 5 — далечен кандидат се допуска само във втори пръстен и винаги остава за ръчна проверка.",
            "Закон 6 — голяма разлика между DXF дължина и зададено L се допуска само при пълна верига с доказан равномерен схематичен мащаб.",
            "Закон 7 — формата се сравнява по относителни ъгли и дължини от успешните контролни тестове, независимо от завъртането.",
            "Закон 8 — посоката на изчертаване и огледалният вариант се разпознават; забранено огледало се маркира за ръчен контрол.",
            "Закон 9 — валиден армировъчен надпис може да бъде BLOCK ATTRIB, TEXT или MTEXT.",
            "Закон 10 — гредово средно N8 с два сегмента, дълъг сегмент L-15 и късо рамо се нормализира като винкел 15 cm под 90°; суровият DXF остава видим за одит.",
            "Закон 11 — силен кандидат не означава автоматично одобрение.",
            "Закон 12 — слой без кодиран диаметър може да даде силен геометричен кандидат само при точна MASTER форма, точна дължина и ясно предимство пред следващия кандидат; Ø остава от надписа.",
            "Закон 13 — контекстни надписи без пълен брой, диаметър и числова дължина не се броят като нерешени позиции.",
            "Закон 14 — съставно стреме F006 се допуска само при строг модел: четири страни на правоъгълен контур и два отделени паралелни ключа; стойността след x е заявената производствена L и се приема само ако отпечатаните ширина, височина и кука възпроизвеждат уравнението L=2×(ширина+височина+кука), с максимум 2 cm изрично видимо проектантско закръгляне; суровият схематичен DXF габарит не замества L.",
            "Закон 15 — пълна MASTER верига има предимство пред неин единичен сегмент или непълен под-път.",
            "Закон 16 — близки резултати между две MASTER форми се показват като конфликт, а не като уверено грешно разпознаване.",
            "Закон 17 — една малка чертожна празнина може да се свърже само между точно два неразклонени компонента, при единствен възможен мост, точна L и валидна MASTER форма; резултатът винаги остава за ръчна проверка.",
            "Закон 18 — LWPOLYLINE дъги се нормализират като огъвки само при доказана допирателност между два прави участъка; останалите криви не се превръщат мълчаливо в прави сегменти.",
            "Закон 19 — специализиран слой за извита армировъчна ос се допуска само чрез изрично конфигуриран alias; това не разрешава общи схематични слоеве.",
            "Закон 20 — когато първият кандидат бъде доказано отхвърлен като чужда геометрия, може да се използва само свободен вече валидиран резервен кандидат; по подразбиране възстановената връзка остава за ръчна проверка и никога не е автоматично одобрение.",
            "Закон 21 — конфликт за една и съща DXF геометрия в еднакъв толерансен диапазон се разрешава само ако един надпис има едновременно поне 2 cm по-добро съвпадение по L, поне 8 чертожни единици по-близка геометрия и поне 0,04 по-добър общ резултат; иначе конфликтът остава видим.",
            "Закон 22 — C/S-форма от специализиран слой ESove се свързва с F007 само при една нормализирана допирателна дъга, завой 135°±8°, отношение на видимите рамена 2:1±25% и обща L в рамките на 2 cm; производствените параметри се възстановяват като L1+L2+L1, а тесният силен радиус важи само за този пълен набор доказателства.",
            "Закон 23 — близки кандидати за една и съща еднозначна MASTER форма могат да се разделят по размери A–E само когато водещият кандидат има пълно уравнение към общата L, конкурентът няма размерно доказателство и водещият е едновременно по-близък и с по-добър резултат.",
            "Закон 24 — свободен резервен кандидат след глобално разпределение може да стане силен само при точни A–E, точна обща L, еднозначен MASTER, съвпадащ Ø от слоя, силна близост и ясно предимство; това пак не е автоматично одобрение.",
            "Закон 25 — табличен ARM_N/ARM_NX/ARM_NXX надпис може да използва отдалечена геометрия от легенда само чрез един и същ глобален позиционен ключ: точно един N…L=… текст до легендното кръгче, точно съвпадение на Ø и L, точно едно кръгче на реда на табличния надпис със същия ключ и един силен недвусмислен MASTER кандидат; връзката е референция към легенда, а не локална собственост на геометрията.",
            "Закон 26 — производствената L на LWPOLYLINE/POLYLINE/ARC се сравнява по физическата ос: правите ръбове са хорди, а всеки bulge/ARC участък участва с точната си дъгова дължина; нормализираната топологична дължина не замества L.",
            "Закон 27 — отдалечено стреме се свързва по дължина само в отделния конфигуриран радиус, на точен слой и с еднозначна F006 форма; при повече от едно съвпадение се приема само една пълна редица от еднакви членове с допустима стъпка и брой, иначе случаят остава нерешен.",
            "Закон 28 — доказан позиционен ключ от легендата има приоритет пред слаб локален кандидат само за ред за ръчен преглед или рисков ред и само при същите точни проверки за ключ, кръгче, Ø, L и еднозначен MASTER; легендната геометрия е референция и не участва в локалното разпределение на собственост.",
            "Закон 29 — разширеният пропорционален допуск за L се прилага само след еднозначно разпознаване като конфигуриран прав прът F001 и е ограничен с абсолютен таван; всички огънати форми запазват общия точен допуск.",
            "Закон 30 — F003 и F005 са отделни производствени подтипове: изричен надпис „фиба/фиби“ избира F005, а „отворен детайл/U-форма“ избира F003 само когато брой и диаметър водят до една-единствена U-сигнатура; без такова доказателство еднаквата геометрия остава MASTER двусмислие.",
            "Закон 31 — локален семантичен префикс „стр.“ или „фиби“ се свързва еднократно с най-близкия пълен ARM_N надпис; локално притежаваните коти не могат да бъдат използвани от съседен детайл.",
            "Закон 32 — закръглено стреме F006 се допуска само при доказана топология от четири допирателни прави страни, четири четвърт-дъги и два паралелни ключа, плюс еднозначно локално уравнение L=2×(ширина+височина+кука); при липса на който и да е елемент резултатът остава нерешен.",
            "Закон 33 — в легенда с равен брой ключове и спецификации и поне една двойка, решена по пряко доказателство, последната свободна двойка е определена и се приема без радиуса, но със същата взаимна еднозначност и същия отстъп пред втория; тя пак минава през проверките за геометрия, Ø, L и еднозначна MASTER форма, оспорвана спецификация не участва, и остатъкът е най-много един, за да не се верижи.",
        )


DEFAULT_RECOGNITION_POLICY = RecognitionPolicy()
