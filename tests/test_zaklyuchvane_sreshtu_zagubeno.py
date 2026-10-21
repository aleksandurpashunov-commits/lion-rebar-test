# -*- coding: utf-8 -*-
"""Заключване срещу загубени актуализации — с два ИСТИНСКИ процеса (§ 7.4).

Задание от 30.09.2026: „Иска се транзакция или заключване и ДОКАЗАТЕЛСТВО, че
няма загубено решение при два процеса."

ЗАЩО АТОМАРНИЯТ ЗАПИС НЕ СТИГА. ``files.atomic_write_text`` е ``mkstemp`` +
``fsync`` + ``os.replace``: файлът никога не е наполовина записан. Но двата
процеса правят

    A чете (N) → B чете (N) → A записва (N+1) → B записва (N+1)

и решението на A изчезва при напълно атомарни записи. Атомарността пази
ФАЙЛА, заключването пази РЕШЕНИЕТО.

Този файл не мери през отчета на програмата: сверява се съдържанието на диска,
след като двата процеса са приключили.

ПОЛОЖИТЕЛНА КОНТРОЛА. Тестът за незаключения път е нарочен: той доказва, че
мярката МОЖЕ да се провали. Без него „няма загубено решение" би било вярно и
когато кодът изобщо не се е състезавал. Затова и двата процеса минават през
бариера и прозорецът между четенето и записа е умишлено отворен.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import lion_rebar
from lion_rebar.session.decision_contract import JOURNAL_NAME

КОРЕН_НА_КОДА = str(Path(lion_rebar.__file__).resolve().parents[1])

#: Колко записа прави ВСЕКИ от двата процеса.
НА_ПРОЦЕС = 25

БАРИЕРА = '''
import os, time
from pathlib import Path


def бариера(папка, мой, общо, таймаут=30.0):
    """Изчаква всички процеси да стигнат дотук. Без изчакване няма състезание."""

    папка = Path(папка)
    папка.mkdir(parents=True, exist_ok=True)
    (папка / ("ready_%s" % мой)).write_text("1", encoding="utf-8")
    краен = time.monotonic() + таймаут
    while len(list(папка.glob("ready_*"))) < общо:
        if time.monotonic() >= краен:
            raise SystemExit("БАРИЕРАТА НЕ СЕ ЗАТВОРИ: няма състезание, мярката е негодна")
        time.sleep(0.01)
'''

def _пусни(скрипт: Path, *аргументи: str) -> list[subprocess.Popen]:
    среда = dict(os.environ)
    среда["PYTHONPATH"] = КОРЕН_НА_КОДА + os.pathsep + среда.get("PYTHONPATH", "")
    среда["PYTHONIOENCODING"] = "utf-8"
    return [
        subprocess.Popen(
            [sys.executable, str(скрипт), *аргументи, str(индекс)],
            env=среда,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        for индекс in (0, 1)
    ]


def _изчакай(
    процеси: list[subprocess.Popen],
    *,
    позволи_провал: bool = False,
) -> list[str]:
    """Изчаква двата процеса. Връща стандартната грешка на всеки."""

    грешки: list[str] = []
    for процес in процеси:
        изход, грешка = процес.communicate(timeout=180)
        текст = грешка.decode("utf-8", "replace")
        грешки.append(текст)
        if not позволи_провал:
            assert процес.returncode == 0, (
                изход.decode("utf-8", "replace") + "\n" + текст
            )
    return грешки


def _напиши(път: Path, съдържание: str) -> Path:
    път.write_text(БАРИЕРА + съдържание, encoding="utf-8")
    return път


def test_zaklyucheniyat_zapis_ne_gubi_nito_edna_promyana(tmp_path):
    """50 промени от два процеса стигат до диска всичките."""

    цел = tmp_path / "брояч.json"
    скрипт = _напиши(
        tmp_path / "работник_заключен.py",
        '''
import sys
from pathlib import Path

from lion_rebar.utils.process_lock import update_json_under_lock

цел, бариерна_папка, брой, мой = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
бариера(бариерна_папка, мой, 2)
for индекс in range(брой):
    update_json_under_lock(
        цел,
        lambda текущо: {"записи": list((текущо or {}).get("записи", [])) + ["%s-%d" % (мой, индекс)]},
        default={"записи": []},
    )
''',
    )
    _изчакай(_пусни(скрипт, str(цел), str(tmp_path / "бариера1"), str(НА_ПРОЦЕС)))

    записи = json.loads(цел.read_text(encoding="utf-8"))["записи"]
    assert len(записи) == 2 * НА_ПРОЦЕС
    assert len(set(записи)) == 2 * НА_ПРОЦЕС
    for мой in ("0", "1"):
        assert sum(1 for запис in записи if запис.startswith(мой + "-")) == НА_ПРОЦЕС


def test_nezaklyucheniyat_zapis_gubi_promeni(tmp_path):
    """ПОЛОЖИТЕЛНА КОНТРОЛА: същата мярка върху атомарен запис БЕЗ заключване.

    Този тест трябва да види загуба. Ако не види, горният тест не доказва нищо
    — значи двата процеса не са се състезавали.
    """

    цел = tmp_path / "брояч.json"
    скрипт = _напиши(
        tmp_path / "работник_незаключен.py",
        '''
import json, sys, time
from pathlib import Path

from lion_rebar.utils.files import atomic_write_text

цел, бариерна_папка, брой, мой = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
бариера(бариерна_папка, мой, 2)
for индекс in range(брой):
    текущо = {"записи": []}
    if цел.is_file():
        try:
            текущо = json.loads(цел.read_text(encoding="utf-8"))
        except ValueError:
            текущо = {"записи": []}
    записи = list(текущо.get("записи", []))
    записи.append("%s-%d" % (мой, индекс))
    time.sleep(0.01)
    atomic_write_text(цел, json.dumps({"записи": записи}, ensure_ascii=False))
''',
    )
    грешки = _изчакай(
        _пусни(скрипт, str(цел), str(tmp_path / "бариера2"), str(НА_ПРОЦЕС)),
        позволи_провал=True,
    )

    # Незаключеният път се проваля по ТРИ начина и всеки от трите е дефект:
    # предишната промяна изчезва; самият ``os.replace`` пада, защото другият
    # процес държи целта отворена; или четенето пада, защото ``os.replace``
    # сменя целта точно докато другият я отваря. Измерено в Windows 11 на
    # 01.10.2026 — и трите се видяха. Мярката иска поне едно от трите.
    паднал = any("PermissionError" in текст or "Traceback" in текст for текст in грешки)
    загубени = 2 * НА_ПРОЦЕС
    try:
        записи = json.loads(цел.read_text(encoding="utf-8"))["записи"]
        загубени = 2 * НА_ПРОЦЕС - len(записи)
    except (OSError, ValueError):
        паднал = True
    assert загубени > 0 or паднал, (
        "Без заключване не е загубено нищо и нищо не е паднало — мярката не "
        "може да се провали и затова не доказва заключването."
    )


def test_dva_protsesa_ne_gubyat_reshenie_v_zhurnala(tmp_path):
    """Истинският път: два процеса пишат решения за ЕДИН и същ handle."""

    корен = tmp_path / "проект"
    (корен / "working").mkdir(parents=True)
    (корен / "working" / "project.json").write_text(
        json.dumps(
            {
                "project_id": "p-lock",
                "name": "ЗАКЛЮЧВАНЕ",
                "engine_id": "lion-rebar-master",
                "engine_version": "0.3.15",
                "engine_sha256": "e" * 64,
                "extensions": {"source_sha256": "f" * 64},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    скрипт = _напиши(
        tmp_path / "работник_журнал.py",
        '''
import sys
from pathlib import Path

from lion_rebar.session.decision_contract import append_project_decision_event

корен, бариерна_папка, брой, мой = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
бариера(бариерна_папка, мой, 2)
for индекс in range(брой):
    append_project_decision_event(
        корен,
        "APPROVED",
        {
            "annotation_handle": "SHARED",
            "master_shape_code": "F001",
            "declared_length_cm": 100 + индекс,
            "approved_by": "процес-%s" % мой,
        },
    )
''',
    )
    _изчакай(_пусни(скрипт, str(корен), str(tmp_path / "бариера3"), str(НА_ПРОЦЕС)))

    редове = [
        json.loads(ред)
        for ред in (корен / "working" / JOURNAL_NAME)
        .read_text(encoding="utf-8")
        .splitlines()
        if ред.strip()
    ]
    очаквани = 2 * НА_ПРОЦЕС
    assert len(редове) == очаквани
    assert len({ред["event_id"] for ред in редове}) == очаквани

    # Ревизиите са точно 1..N: всеки процес е прочел състоянието СЛЕД другия.
    # Еднаква ревизия на два реда е точно загубената актуализация.
    ревизии = sorted(int(ред["decision_revision"]) for ред in редове)
    assert ревизии == list(range(1, очаквани + 1))

    # И връзката назад е цяла: решение с ревизия r отменя точно r-1 предходни.
    for ред in редове:
        assert len(ред["supersedes_event_ids"]) == int(ред["decision_revision"]) - 1

    # Двата автора са запазени — нито едно решение не е изместено от другото.
    автори = [ред["actor"]["recorded_as"] for ред in редове]
    assert автори.count("процес-0") == НА_ПРОЦЕС
    assert автори.count("процес-1") == НА_ПРОЦЕС


def test_dva_protsesa_ne_gubyat_nachertana_ramka(tmp_path):
    """Начертаните рамки са операторско решение и също бяха без заключване.

    ``record_operator_area`` чете целия списък, решава върху прочетеното
    (огъването по съседните рамки) и записва нов списък. Двайсет рамки от два
    процеса трябва да са двайсет на диска.
    """

    скрипт = _напиши(
        tmp_path / "работник_рамки.py",
        '''
import sys
from pathlib import Path

from lion_rebar.export.operator_areas import KIND_BEAM, record_operator_area
from lion_rebar.project.data_identity import DataIdentity

корен, бариерна_папка, брой, мой = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3]), sys.argv[4]
самоличност = DataIdentity(
    project_id="s-9", source_sha256="SHA",
    source_generation_id="G1", analysis_run_id="R1",
)
бариера(бариерна_папка, мой, 2)
отместване = 100000.0 * (1 + int(мой))
for индекс in range(брой):
    основа = отместване + 1000.0 * индекс
    record_operator_area(
        корен,
        box=(основа, 0.0, основа + 500.0, 300.0),
        kind=KIND_BEAM,
        title_handle="T%s-%d" % (мой, индекс),
        title_text="Гр. B.%s.%d 25/55" % (мой, индекс),
        frame_index=6,
        decided_by="процес-%s" % мой,
        note="рамка %d от процес %s за проверката на заключването" % (индекс, мой),
        identity=самоличност,
    )
''',
    )
    рамки_на_процес = 10
    _изчакай(
        _пусни(скрипт, str(tmp_path), str(tmp_path / "бариера4"), str(рамки_на_процес))
    )

    from lion_rebar.export.operator_areas import AREAS_NAME, load_operator_areas
    from lion_rebar.project.data_identity import DataIdentity

    записани = load_operator_areas(
        tmp_path,
        DataIdentity(
            project_id="s-9",
            source_sha256="SHA",
            source_generation_id="G1",
            analysis_run_id="R1",
        ),
    )
    assert len(записани) == 2 * рамки_на_процес
    автори = [област.decided_by for област in записани]
    assert автори.count("процес-0") == рамки_на_процес
    assert автори.count("процес-1") == рамки_на_процес
    # Файлът е цял и четим, а не наполовина записан.
    съдържание = json.loads(
        (tmp_path / "working" / AREAS_NAME).read_text(encoding="utf-8")
    )
    assert len(съдържание["areas"]) == 2 * рамки_на_процес


def test_vtora_nishka_v_sashtiya_protses_ne_vliza_bez_zaklyuchvane(tmp_path):
    """Повторното влизане важи за ЕДНА нишка, не за целия процес.

    Повторното влизане е нужно, защото метод, който държи ключалката, може да
    извика друг върху същия файл. Ако броячът беше общ за процеса, втора НИШКА
    щеше да види „вече заключено" и да влезе в критичната секция, без да
    заключи — тоест работникът на самата програма би подминал заключването.
    """

    import threading

    from lion_rebar.utils.process_lock import FileLockTimeout, exclusive_file_lock

    цел = tmp_path / "нишки.json"
    взето = threading.Event()
    пускай = threading.Event()
    резултат: dict[str, object] = {}

    def държи() -> None:
        with exclusive_file_lock(цел):
            взето.set()
            пускай.wait(10)

    def опитва() -> None:
        try:
            with exclusive_file_lock(цел, timeout=0.3):
                резултат["влязла"] = True
        except FileLockTimeout:
            резултат["влязла"] = False

    първа = threading.Thread(target=държи)
    първа.start()
    assert взето.wait(10), "Първата нишка не взе ключалката."
    втора = threading.Thread(target=опитва)
    втора.start()
    втора.join(30)
    пускай.set()
    първа.join(30)

    assert резултат.get("влязла") is False


def test_taymautat_e_otkaz_a_ne_tiho_prodalzhavane(tmp_path):
    """Зает файл по-дълго от позволеното е ИЗКЛЮЧЕНИЕ, не мълчалив запис."""

    from lion_rebar.utils.process_lock import FileLockTimeout, exclusive_file_lock

    цел = tmp_path / "зает.json"
    скрипт = tmp_path / "дръж.py"
    скрипт.write_text(
        '''
import sys, time
from pathlib import Path

from lion_rebar.utils.process_lock import exclusive_file_lock

цел = Path(sys.argv[1])
with exclusive_file_lock(цел):
    Path(sys.argv[2]).write_text("1", encoding="utf-8")
    time.sleep(float(sys.argv[3]))
''',
        encoding="utf-8",
    )
    знак = tmp_path / "взето"
    среда = dict(os.environ)
    среда["PYTHONPATH"] = КОРЕН_НА_КОДА + os.pathsep + среда.get("PYTHONPATH", "")
    процес = subprocess.Popen(
        [sys.executable, str(скрипт), str(цел), str(знак), "5"],
        env=среда,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        краен = time.monotonic() + 30
        while not знак.is_file():
            if time.monotonic() >= краен or процес.poll() is not None:
                pytest.fail("Другият процес не взе ключалката.")
            time.sleep(0.01)
        with pytest.raises(FileLockTimeout):
            with exclusive_file_lock(цел, timeout=0.3):
                pytest.fail("Заключването пусна втори писател в критичната секция.")
    finally:
        процес.kill()
        процес.communicate(timeout=60)
