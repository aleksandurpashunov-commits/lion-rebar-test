# Windows операторски чеклист — LION REBAR v0.1.44.20-rc43

Дата: 08.09.2026 г.  Engine: 0.3.14.

Тествай в отделна папка и върху копие на проектите. Не презаписвай rc42.

## 1. Стартиране

- [ ] PASS / [ ] FAIL — Разархивирай rc43 в отделна папка.
- [ ] PASS / [ ] FAIL — Ако няма `.venv`, стартирай
  `INSTALL_LION_REBAR.bat`; после `START_LION_REBAR.bat`.
- [ ] PASS / [ ] FAIL — Заглавието показва `v0.1.44.20-rc43`.

Това е source Windows пакет, не готов `.exe`. За executable използвай
`BUILD_EXE.bat` след успешна инсталация; той трябва да завърши и dist smoke
tests да са зелени.

## 2. Основният proxy DXF

Създай нов тестов проект и импортирай:

`39_83_shaibi_+21.48+24.78  do +24.78+28.08.dxf`

- [ ] PASS / [ ] FAIL — Import report показва 341 proxy instances.
- [ ] PASS / [ ] FAIL — Показани са 3 803 от 3 803 намерени примитива.
- [ ] PASS / [ ] FAIL — Типовете са POLYLINE 1 171, ARC 439, CIRCLE 708 и
  TEXT 1 485.
- [ ] PASS / [ ] FAIL — Complete е 341; partial/missing/unreadable и
  unsupported са 0.
- [ ] PASS / [ ] FAIL — Няма общо мълчаливо предупреждение; ако друг файл има
  проблем, отчетът показва layer, handle/instance и конкретна причина.

## 3. Визуално сравнение

- [ ] PASS / [ ] FAIL — Използвай „Покажи целия чертеж“/Fit: новата proxy
  графика участва в bounds и не е отрязана.
- [ ] PASS / [ ] FAIL — Приближи горния ляв сектор: диагоналните детайли се
  виждат на правилните места.
- [ ] PASS / [ ] FAIL — Приближи под десните сечения: дългите пръчки се
  виждат и не са разместени.
- [ ] PASS / [ ] FAIL — Дъгите и заоблените/bulge участъци са криви, не са
  заменени с прави хорди.
- [ ] PASS / [ ] FAIL — Текстовете са на местата си, с правдоподобна височина
  и завъртане; цветовете са близки до CAD и четими на светлия фон.
- [ ] PASS / [ ] FAIL — CENTER/RIGHT/FIT/ALIGNED текст и MTEXT attachment са
  на правилните anchor точки. Произволният oblique shear е приблизителен
  italic и не се очаква да е пикселно еднакъв с AutoCAD.
- [ ] PASS / [ ] FAIL — В r-10 ACI 5/6 и true-color proxy текстовете не са
  заменени с общ тъмносив цвят.
- [ ] PASS / [ ] FAIL — Изключи и включи `arm_shaibi`, `C_Arm_steni`,
  `arm_koloni` и `C_ARM_Gredi`: тяхната proxy графика следва видимостта.
- [ ] PASS / [ ] FAIL — При вложен block скрий външния INSERT слой и включи
  вътрешния: графиката трябва да остане скрита и да не влияе на Fit.

## 4. Избор и недублиране

- [ ] PASS / [ ] FAIL — Generic display-only линии/текстове се виждат, но не
  се избират като арматурни позиции и не отварят инженерни данни.
- [ ] PASS / [ ] FAIL — В r-10 разпознатите позиции остават избираеми и
  маркирането им работи.
- [ ] PASS / [ ] FAIL — В r-10 няма двойно изчертаване върху strict proxy
  геометрията; import report показва 2 712 намерени, 1 221 потиснати и
  1 491 display-only примитива.
- [ ] PASS / [ ] FAIL — Пакетният анализ/Excel не съдържа нови редове само
  заради показаната proxy графика.

## 5. Повторно отваряне и решения

Работи върху копие на r-10 или друг завършен проект.

- [ ] PASS / [ ] FAIL — Запиши, затвори и отвори проекта два пъти: визуалната
  графика не се удвоява.
- [ ] PASS / [ ] FAIL — Одобрените връзки, ръчните поправки и Review Center
  решения са запазени.
- [ ] PASS / [ ] FAIL — При първо отваряне на стар rc42 проект кешът се
  обновява. Ако има стар freeze, изпълни отново „Финален контрол“, защото
  извлеченият cache hash вече включва display канала.
- [ ] PASS / [ ] FAIL — След нов успешен Final Control export-ът завършва без
  добавени proxy-only количества.

## 6. Контролни количества

- [ ] PASS / [ ] FAIL — r-10: 132 actionable, 130 approved, 2 539 общо броя
  и 2 535 approved броя.
- [ ] PASS / [ ] FAIL — r-11: 9/9 одобрени, 4 584 броя и приблизително
  5 877.590902748 kg.
- [ ] PASS / [ ] FAIL — Ако разполагаш с r-12, запиши неговите before/after
  позиции, бройки и kg. r-12 не беше наличен за автоматичната проверка.

## 7. Ако има FAIL

Не променяй оригиналния DXF. Запази:

- снимка на целия прозорец и приближената проблемна област;
- import report JSON текста;
- името на слоя и proxy handle/instance от tooltip/диагностиката;
- копие на `working/extracted_entities.json` и последния batch report;
- точната стъпка от този чеклист.

## Автоматична база преди операторския тест

PASS: реалният shaibi DXF — 341 proxy, 3 803/3 803 показани, 0
unsupported/partial/missing/unreadable; strict engineering остава 0.

PASS: r-10 quantity/dedupe/reopen; PASS: r-11 decisions/count/kg; PASS:
synthetic nested/MINSERT, negative OCS/mirrored INSERT, tilted OCS curve
projection, parent-layer AND visibility, cycle/corrupt/unsupported,
curve/color/text-alignment/UI fixtures.

Пълен pytest в Qt offscreen режим: **1 139 събрани = 1 128 passed +
11 skipped, 0 failed**, exit code 0 за 143.27 s. Интерактивният Windows
дисплей и r-12 остават за операторска проверка.

- [ ] ОПЕРАТОРСКА ПРИЕМКА — реалният Windows GUI не е изпълнен в тази
  автоматична сесия; попълни горните PASS/FAIL полета на лаптопа.

Оператор: ____________________  Дата: __________  Общ резултат: PASS / FAIL
