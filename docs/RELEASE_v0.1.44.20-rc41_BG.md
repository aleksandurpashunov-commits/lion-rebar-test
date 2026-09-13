# LION REBAR DESKTOP v0.1.44.20-rc41

Дата: 06.09.2026 г.  Engine: 0.3.14.

## Цел на версията

rc41 поправя случай, при който всички позиции са решени, но Финалният контрол
и Export остават блокирани без видима причина. Проблемът беше във frame/freeze
lifecycle-а, а не в инженерните решения или валидните непроизводствени позиции.

## Установени причини

- r-10: 130 производствени + 2 валидни `NON_PRODUCTION_POSITION` са отчетени
  правилно. Дългият текст „ЗАБЕЛЕЖКИ“ е бил приет погрешно за рамка само защото
  съдържа думите „чертежа/чертежите“. Получената UNKNOWN рамка е блокирала
  freeze manifest-а.
- r-11: всичките 9 позиции са инженерно решени, но проектантът не използва
  стандартното поле `ЧЕРТЕЖ:`. Реалното заглавие е `ВЕРТ.АРМИРОВКА`, поради
  което старата версия не е откривала рамка и е връщала
  `BEAM_FREEZE_NO_FRAMES`.
- И в двата случая UI е показвал само позиционните броячи. Техническото
  `result.issues` не е било изписано, затова операторът е виждал нули и
  подвеждащо указание да се върне в Review Center.

## Реализирано

- Marker за стандартна рамка е само самостоятелното поле `ЧЕРТЕЖ:`; думи в
  свободен текст не създават рамка.
- Markerless еднолистов чертеж се приема само при две независими доказателства:
  - точно едно ясно категорийно заглавие + точно една INSERT рамка на frame
    layer; или
  - `ВЕРТ.АРМИРОВКА` + отделни доказателства за схема и колони/шайби, без
    frame INSERT.
- Само заглавие, две рамки, две конфликтни категории или неясен файл остават
  блокирани. Не се използва largest-rectangle или nearest-text догадка.
- Fallback provenance е `MEDIUM` с точна причина
  `SINGLE_SHEET_TITLE_AND_FRAME_INSERT` или
  `SINGLE_SHEET_TITLE_AND_VERTICAL_SECTIONS`.
- UNKNOWN категория се отхвърля преди запис на frame/beam assignments; не се
  отчита повече като успешно замразена негредова рамка.
- Final Control и Export показват ERROR кодовете и съобщенията на реалните
  технически blockers. Review Center се препоръчва само при действително
  нерешени позиции.
- UNKNOWN-frame диалогът използва реалните импортнати DXF entities, прилага
  вече записаните решения и вече не поглъща тихо грешка от несъществуващото
  поле `self.entities`.
- Успешен UI резултат се допуска само при пълна спецификация и валиден freeze
  lifecycle.

## Автоматична и реална проверка

- Фокусирани rc41 regressions: 20 PASS — strict marker, двуфакторен fallback,
  конфликтни/двойни рамки, UNKNOWN freeze, UI blocker text и реален wiring на
  unknown-frame пътя.
- Съществуващи frame/integrity/vertical/UI regressions: 63 PASS.
- r-10, изолирано копие: Final Control PASS; 132 ledger реда = 130 APPROVED +
  2 NON_PRODUCTION; 130 frame assignments; freeze и export preflight PASS.
- r-10 paired export: `PRODUCTION VERIFIED`; листове „горна“ и „Общо“; 65
  source/global групи; 2535 броя и 7024.117242753 kg са запазени; 130 DXF
  позиции; manifest equation `132 - 2 = 130`; manifest verification = 0
  проблеми.
- r-11, изолирано копие: Final Control PASS; 9/9 APPROVED; 9 vertical
  assignments; 2 вертикални секции; freeze и export preflight PASS.
- r-11 paired export: `PRODUCTION VERIFIED`; листове „вертикали“ и „Общо“;
  8 source групи са окрупнени до 7 общи; 4584 броя и 5877.590902748 kg са
  запазени; 9 DXF позиции; manifest equation `9 - 0 = 9`; manifest verification
  = 0 проблеми.
- Пълен release pytest: 1068 събрани теста = 1057 PASS + 11 SKIPPED;
  exit code 0.
- Оригиналните r-10 и r-11 не са променяни. Всички replay операции са върху
  копия в `verification_projects`.

SHA-256 на крайния ZIP се записва след финалното release packaging.

## Какво остава за Windows операторски тест

Провери с UI върху нови копия на r-10 и r-11: заглавието на версията, текста на
Final Control, наличния freeze path, отварянето на Excel и DXF в Microsoft
Excel/AutoCAD и поведението на действително UNKNOWN рамка. Краткият протокол е
`WINDOWS_ACCEPTANCE_rc41.md`.
