# Windows acceptance — LION REBAR v0.1.44.20-rc35

Дата: 02.09.2026 г.

## Резултат

**PASS** за изолираното копие на реалния проект R-008 и финалния rc35 Excel. Оригиналният операторски проект `C:\LION_TEST_A\R-008` не е променян.

## Final Control

- Реални ACTIONABLE позиции: **586**.
- Одобрени производствени позиции: **582**.
- Доказани NON_PRODUCTION позиции: **4**.
- Общо отчетени: **586/586**.
- Нерешени позиции: **0**.
- Data-identity грешки: **0**.
- Двойни/споделени geometry ownership-и: **0**.
- Позиции без валидна MASTER форма: **0**.
- Замразени frame/beam assignments: **582**.
- Замразени vertical assignments: **126**.
- Замразени vertical sections: **12**.
- Freeze status: **VALID**.
- Export lifecycle blockers: **0**.

Четирите NON_PRODUCTION handles са:

- `11C51::BJ-FI-L`
- `1222A::BJ-FI-L`
- `DB88::BJ-FI-L`
- `EC0D::BJ-FI-L`

Те отсъстват от производствените source редове, total редовете и annotation map-а.

## Покритие на целия етаж

- Рамки: **7**.
- По рамки: **72 / 72 / 74 / 72 / 1 / 165 / 126** позиции.
- По категории: BOTTOM 72, BOTTOM_EXTRA 72, TOP 74, TOP_EXTRA 72, DETAILS 1, BEAMS 165, VERTICALS 126.
- Форми: F001 359, F003 61, F004 40, F005 8, F006 96, F007 17, F008 1.
- Гредови позиции: **165**, разпределени в **32** гредови секции.
- Вертикални позиции: **126**, разпределени еднозначно в **12** секции.
- Глобални производствени идентичности: **168**.

## Excel и traceability

- Листове: **8** — долна, долна усилване, горна, горна усилване, детайли, греди, вертикали и Общо.
- Source групи: **317**.
- Source annotation map: **582/582**, всяка производствена позиция точно веднъж.
- Total редове: **168**.
- Total → source references: **317/317**.
- Vertical source групи: **85**; окрупнени повторения в рамките на секция: **41**.
- Cross-section merge в source листа: **не**.
- Вградени MASTER PNG: **485 = 317 source + 168 total**.
- Trace schema: `lion-rebar-excel-traceability/v3`.

## Четими F006/F007 размери

- Картографирани и проверени F006/F007 изображения в workbook-а: **96**.
- Source F006: **64**; source F007: **10**.
- Total F006: **21**; total F007: **1**.
- Уникални визуални варианта: **20 = 19 F006 + 1 F007**.
- Всичките 20 уникални варианта са прегледани отблизо.
- Не са намерени отрязани, припокрити, твърде малки или далечни размерни надписи.
- F006 показва ясно ширина, височина и кука.
- F007 показва ясно двете рамена и средния сегмент.

Artifact-tool render-ът потвърди структурата и layout-а на всички листове. Импортираните floating PNG не се рисуват от неговия preview renderer, затова същите anchors бяха картографирани чрез artifact-tool и точните workbook media PNG бяха визуално проверени отделно.

## Формули и автоматични проверки

- Формули, проверени чрез artifact-tool: **1 949**.
- Formula/value errors: **0**.
- R-008 fail-closed acceptance harness: **PASS**.
- Harness unit regressions: **3/3 PASS**.
- Stale-existing-DXF-handle regression: **PASS**.
- Engineer-decision targeted suite: **24/24 PASS**.
- Пълен `pytest -q`: **100%, exit code 0**.

## SHA-256

- XLSX: `8CB4B16CE1D13F7BFEBCC8D564202FC7D812CD9C354F7E4E10C00A274A450A54`
- Excel trace: `44B7171C6C7F9682D4E521115B0D961A5DD75AC2CED1DD141D94B2E0D5FFD238`
- Final Control freeze: `D528E681DB233E19513D09DADA92A1DE553647B3CE6C6C580B2ABE5839CA9C94`
- Final integrity report: `0A83C2C8987366277F26F6DB5B8607C150FC230C22D4EFE192A8425469A1D377`

## Заключение

Final Control вече приема коректно валидните vertical секции по B/В без отслабване на fail-closed правилата. Маркирането не позволява DIMENSION или далечно очертание да стане производствена ос. Стар geometry handle не може да се възстанови в режим „Без DXF връзка“. Всички стремена и S/C форми в Excel имат четими размери.
