"""Операторът трябва да ВИЖДА размерите, за да реши.

Открито при реален пробег: позиция със статус „ЛИПСВА MASTER“ показваше
„Форма: — —“ и нищо повече. Размерите на стремето съществуваха в чертежа
като котировки, но никъде не стигаха до екрана, затова инженерното
решение не можеше да бъде довършено.
"""
from __future__ import annotations

from pathlib import Path

SOURCE = Path("lion_rebar/ui/review_center.py").read_text(encoding="utf-8")
REPORT = Path("lion_rebar/dxf/batch_geometry_report.py").read_text(encoding="utf-8")


def _render(row):
    namespace = {"_escape": lambda value: str(value)}
    start = SOURCE.index("def _dimension_evidence_html")
    end = SOURCE.index("\n\n\n", start)
    exec(SOURCE[start:end], namespace)  # noqa: S102
    return namespace["_dimension_evidence_html"](row)


def test_stirrup_dimensions_are_shown():
    html = _render({"production_segments_cm": [10, 20, 35, 20, 35, 10]})
    assert "35 x 20" in html
    assert "кука 10" in html


def test_three_segment_form_is_shown():
    html = _render({"production_segments_cm": [15, 150, 15]})
    assert "15 / 150 / 15" in html


def test_source_and_confidence_are_shown():
    html = _render({
        "production_segments_cm": [35, 20, 10],
        "dimension_derived_evidence": {
            "source": "НАДПИСАНИ КОТИРОВКИ", "confidence": "HIGH",
            "bend_allowance_cm": 5},
    })
    assert "НАДПИСАНИ КОТИРОВКИ" in html
    assert "HIGH" in html
    assert "добавка за огъване 5" in html


def test_nearby_dimensions_help_when_derivation_failed():
    """Дори без изведени размери операторът вижда какво има в чертежа."""

    html = _render({"nearby_dimensions_cm": [20, 10, 35, 165]})
    assert "Котировки наблизо" in html
    assert "165" in html


def test_missing_dimensions_are_stated_explicitly():
    html = _render({})
    assert "няма прочетени котировки" in html


def test_panel_calls_the_helper():
    assert "_dimension_evidence_html(row)" in SOURCE


def test_nearby_dimensions_are_recorded_even_on_failure():
    """Записват се ПРЕДИ извеждането, за да ги има и при отказ."""

    start = REPORT.index("def _apply_dimension_derived_production_sizes")
    body = REPORT[start:start + 18000]
    # Записът става за ВСЯКА позиция, преди проверката за MASTER форма --
    # тоест и когато извеждането после се откаже.
    record = body.index('row["nearby_dimensions_cm"]')
    master_check = body.index('if master != "F006"')
    assert record < master_check


def test_nearby_values_are_snapped_and_filtered():
    namespace: dict = {}
    start = REPORT.index("def _nearby_dimension_values")
    end = REPORT.index("_MATCHER_PRODUCTION_DIMENSION_STRATEGIES")
    exec(REPORT[start:end], namespace)  # noqa: S102
    values = namespace["_nearby_dimension_values"](
        (0.0, 0.0),
        [{"measurement": 209.8, "point": (10.0, 0.0)},
         {"measurement": 2.0, "point": (20.0, 0.0)},
         {"measurement": 20.0, "point": (30.0, 0.0)}],
    )
    assert 210.0 in values
    assert 2.0 not in values


def test_dimensions_are_rendered_first_in_the_panel():
    """Панелът е нисък: размерите трябва да са преди всичко останало."""

    # Първото срещане на setHtml е съобщението за празна опашка; търси се
    # блокът, който реално рендира позиция.
    render = SOURCE.index("_dimension_evidence_html(row)}</div>")
    window = SOURCE[max(0, render - 700):render + 700]
    heading = window.index("</h3>")
    evidence = window.index("_dimension_evidence_html(row)")
    status = window.index("Статус:")
    assert heading < evidence < status


def test_details_panel_has_minimum_height():
    assert "self.details.setMinimumHeight" in SOURCE


def test_position_number_message_does_not_contradict_the_legend():
    """Номер на позиция и легендно кръгче са различни неща."""

    panel = Path("lion_rebar/ui/entity_panel.py").read_text(encoding="utf-8")
    assert "Легендните кръгчета" in panel
    assert "не се използват близки кръгчета" not in panel


def test_suggestion_is_offered_when_shape_is_unknown():
    """Позиция без MASTER форма получава ПРЕДЛОЖЕНИЕ, не производствен размер."""

    html = _render({
        "suggested_shape_code": "F006",
        "suggested_segments_cm": [25, 20, 10],
    })
    assert "ПРЕДЛОЖЕНИЕ" in html
    assert "25 x 20" in html
    assert "изисква потвърждение" in html


def test_suggestion_does_not_override_proven_sizes():
    html = _render({
        "production_segments_cm": [10, 20, 35, 20, 35, 10],
        "suggested_segments_cm": [99, 99, 99],
    })
    assert "35 x 20" in html
    assert "ПРЕДЛОЖЕНИЕ" not in html


def test_unconfirmed_legend_key_is_not_shown():
    """Чуждо кръгче подвежда повече, отколкото помага."""

    html = _render({"legend_key": "5b"})
    assert "5B" not in html


def test_suggestions_are_not_production_values():
    """ВСЯКО място, което пише предложение, носи обосновка защо не е доказано.

    Дотук се проверяваше само първото срещане. На 22.09.2026 се появи второ
    (размерите на форма, родена от случай) и тестът щеше да мине или да падне
    според реда в файла, не според това дали мястото е обосновано.

    Приема се или старият мотив („без форма не може да се твърди какво е“),
    или изричното „остават предложение“ — и двете казват едно: стойността не
    е производствена, докато операторът не я потвърди.
    """
    from pathlib import Path

    source = Path("lion_rebar/dxf/batch_geometry_report.py").read_text(encoding="utf-8")
    # Всеки от тези казва едно и също: стойността НЕ е производствена, докато
    # операторът не я потвърди. Списъкът е на два езика, защото такъв е и
    # файлът — по-старите места са писани на английски.
    мотиви = (
        "не може да се твърди какво е",
        "остават предложение",
        "само като предложение",
        "operator still reviews and confirms",
    )

    места = []
    от = 0
    while True:
        индекс = source.find('row["suggested_shape_code"]', от)
        if индекс < 0:
            break
        места.append(индекс)
        от = индекс + 1
    assert места, "никой не пише предложена форма — тестът е загубил предмета си"

    редове = source.splitlines()

    def функцията_около(индекс: int) -> str:
        """Текстът на функцията, в която стои този знак."""

        ред = source[:индекс].count(chr(10))
        начало = 0
        for n in range(ред, -1, -1):
            if редове[n].startswith("def "):
                начало = n
                break
        край = len(редове)
        for n in range(начало + 1, len(редове)):
            if редове[n].startswith(("def ", "class ")):
                край = n
                break
        return chr(10).join(редове[начало:край])

    for индекс in места:
        # Разстоянията се нормализират: мотивът често е пренесен на нов ред и
        # точният низ не се среща, макар изречението да е там.
        тяло = " ".join(функцията_около(индекс).split())
        assert any(" ".join(мотив.split()) in тяло for мотив in мотиви), (
            "предложение без обосновка във функцията около знак %d" % индекс
        )


DIALOG = Path("lion_rebar/ui/engineer_decision_dialog.py").read_text(encoding="utf-8")


def test_dialog_prefills_the_suggested_shape():
    """Операторът не бива да преписва предложението от единия екран в другия."""

    assert 'row.get("suggested_shape_code")' in DIALOG
    start = DIALOG.index("preferred = str(")
    window = DIALOG[start:start + 400]
    assert 'row.get("master_shape_code")' in window
    recognised = window.index('row.get("master_shape_code")')
    suggested = window.index('row.get("suggested_shape_code")')
    assert recognised < suggested, "разпознатата форма има предимство"


def test_dialog_prefills_the_suggested_segments():
    assert 'row.get("suggested_segments_cm")' in DIALOG


def test_stirrup_suggestion_is_expanded_to_six_segments():
    """F006 се въвежда като кука + страна + страна + страна + страна + кука."""

    start = DIALOG.index("suggested_segments = list(")
    window = DIALOG[start:start + 900]
    assert "[hook, height, width, height, width, hook]" in window


def test_suggestion_is_visually_marked_in_the_dialog():
    assert "_is_suggestion" in DIALOG
    assert "Провери го срещу" in DIALOG


def test_proven_segments_win_over_suggestion():
    start = DIALOG.index("segments = (")
    window = DIALOG[start:start + 700]
    proven = window.index('row.get("production_segments_cm")')
    suggested = window.index("suggested_segments")
    assert proven < suggested


VIEW = Path("lion_rebar/ui/drawing_view.py").read_text(encoding="utf-8")


def test_dimension_values_are_drawn_in_the_viewport():
    """Котировката се рисува КАКТО В AUTOCAD: линия ПЛЮС число.

    Досега се чертаеше само линията и операторът виждаше чертички без
    размери — при вертикалните елементи точно числата носят размерите на
    стремената.
    """

    start = VIEW.index('if kind == "DIMENSION":')
    body = VIEW[start:start + 3200]
    assert 'geometry.get("measurement")' in body
    assert "QGraphicsSimpleTextItem" in body
    assert "QGraphicsLineItem" in body


def test_dimension_without_measurement_still_draws_the_line():
    start = VIEW.index('if kind == "DIMENSION":')
    body = VIEW[start:start + 3200]
    assert "if measurement is not None:" in body


def test_dimension_group_is_returned_not_bare_line():
    start = VIEW.index('if kind == "DIMENSION":')
    body = VIEW[start:start + 3200]
    assert "QGraphicsItemGroup()" in body
    assert "group.childItems()" in body
