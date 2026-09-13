# Автоматичен трансформационен regression — v0.1.36

## Цел

За всяка активна `APPROVED` или `LOCKED` форма Engine автоматично генерира
детерминирана тестова матрица. Няма случайни стойности и няма ръчно написани
тестове по код на форма.

Покриват се:

- завъртане;
- обратна посока на обхождане;
- разрешено огледало без допълнителен блокер;
- забранено огледало като откриваем кандидат, който задължително изисква
  човешки контрол;
- равномерен схематичен мащаб;
- безопасни варианти на параметричните дължини;
- `LWPOLYLINE` и верига от отделни `LINE`;
- малко контролирано отклонение на координатите.

По подразбиране се използва pairwise матрица. Тя покрива всяка двойка
стойности между всички оси, без броят на тестовете да нараства като пълното
декартово произведение. За фокусиран release audit може да се зададе
`coverage="EXHAUSTIVE"`.

## Публичен API

```python
from lion_rebar.engine.test_runner import (
    generate_engine_transformation_fixture,
    run_engine_transformation_tests,
)

fixture = generate_engine_transformation_fixture(engine)
report = run_engine_transformation_tests(engine, fixture)
assert report.passed
```

Директният API е:

```python
from lion_rebar.engine.transformation_regression import (
    TransformationRegressionConfig,
    TransformationRegressionFixture,
    generate_transformation_regression_fixture,
    run_transformation_regression,
)
```

`fixture.to_json()` създава стабилен JSON. Зареждането е с
`TransformationRegressionFixture.from_dict(...)`.

## Съвместимост с досегашния audit

Старият `audit_engine_recognition(engine)` и досегашният му брой случаи не са
променени. Новият генератор се включва през:

```python
from lion_rebar.engine.recognition_audit import (
    audit_engine_generated_regression,
)

audit = audit_engine_generated_regression(engine)
```

Това позволява новият gate да се активира контролирано, без да се чупят
старите отчети и тестове.

## Правила за безопасност

- Фиксиран сегмент не се променя от параметричните варианти.
- Повторен код, например `L1` на две рамена, получава еднакъв множител.
- Ако legacy Engine не е записал изрично параметричните кодове, вариант се
  генерира само ако текущият общ matcher договор доказано го приема.
- При изричен `recognition.parametric_length_codes` неуспешният вариант остава
  блокиращ regression тест.
- Еднакви/застъпени върхове получават еднакво координатно отклонение, за да не
  се създава изкуствен микросегмент.
- Криволинейните сегменти се маркират изрично като
  `CHORD_PROXY_FOR_TOPOLOGY_REGRESSION`. Това проверява топологията, но не
  замества бъдещ отделен radius/arc matcher тест.
- Всяка одобрена форма се проверява и огледално. При
  `mirror_allowed=false` правилният резултат е намерен код плюс
  `mirror_requires_review=true`, а не изчезнала форма или тихо приемане.
