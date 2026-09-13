# RC29 — ДОКАЗАТЕЛСТВО ЗА ПОПРАВКИТЕ ОТ ОДИТА

Дата: 2026-08-29  
Версия: `0.1.44.20-rc29`

## Потвърдени поправки

1. Реалният Excel trace използва `frames` и вече се сравнява със freeze-а.
2. `EXPORT_CONFIG`, `EXCEL_TRACE`, `DXF_TRACE` и
   `FINAL_CONTROL_FREEZE` са задължителни SHA артефакти в export manifest v2.
3. Export config е SHA-защитен и във Final Control freeze-а; вътрешният му
   fingerprint се валидира при четене.
4. UNKNOWN операторските решения достигат automatic batch context, auto
   approval, Final Ledger, freeze, Excel и manifest.
5. F006 собствените котировки не се смесват между контури и минават през
   централната проверка за обща дължина и добавка за огъване.
6. Automatic batch output валидира повторно derived сегментите с Engine.
7. Undo проверява автоматично export history и разрешава само последното
   активно групово действие.

## Негативни сценарии

| Сценарий | Очакван резултат | Резултат |
|---|---|---|
| Trace `BOTTOM/долна` срещу freeze `VERTICALS/вертикали` | Manifest отказ | PASS |
| Липсващ Excel trace | Manifest отказ | PASS |
| Подменен config със стар fingerprint | Export отказ | PASS |
| F006 сегменти 320 cm срещу L=135 cm | Production review/block | PASS |
| F006 сегменти 130 cm срещу L=135 cm | Приета bend добавка | PASS |
| Derived F006 в подменен batch report | Engine length blocker | PASS |
| UNKNOWN→VERTICALS операторско решение | Същата категория в Ledger/export | PASS |
| Undo след verified export | Отказ | PASS |
| Undo на по-стара активна операция | Отказ | PASS |

## Автоматизирана проверка

- Избрани тестове: 815
- Успешни: 805
- Пропуснати поради средата: 10
- Неуспешни: 0
- `compileall`: PASS
- `ruff F821,E9`: PASS
- пълен `ruff F` върху всички променени production модули: PASS

Четири Qt UI test модула и един SVG raster тест не са включени в този Linux
пробег поради липсваща графична/EGL среда. Това не е Windows приемане.

## Неприключили release gates

1. Заключеният реален DXF не е в source пакета. Нов rc29 holdout не е
   изпълнен и не се твърди нулева промяна.
2. Не е изпълнен реален Windows build/EXE операторски тест.
3. Споделената легендна геометрия остава известен архитектурен проблем.

Следователно rc29 е поправен source RC, но не е production-final версия.
