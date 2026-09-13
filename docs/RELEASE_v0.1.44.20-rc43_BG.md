# LION REBAR DESKTOP v0.1.44.20-rc43

Дата: 08.09.2026 г.  Engine: 0.3.14.

## Цел на версията

rc43 отстранява липсващото изобразяване на наличната графика в
`ACAD_PROXY_ENTITY`, без да превръща визуалните примитиви в производствени
позиции. Поправката е направена върху отделно копие на rc42; rc42 не е
презаписан и всички негови H-3, Review Center, Final Control и export
подобрения са запазени.

## 1. Какво вече работеше в rc42

- Оригиналният DXF се пазеше непроменен и при отваряне на проект се
  прочиташе отново от `source/original.dxf`.
- Строгият proxy adapter можеше да създава инженерни записи само при
  доказаните GA_X/GA_Y правила. Този път остава единственият proxy вход към
  разпознаване, количества, тегла, Excel и производствен DXF.
- Ръчните решения и data identity се пазеха отделно от кеша с извлечените
  DXF обекти.
- Разпознатите proxy позиции имаха собствен избор и маркиране.

Оставащият дефект беше, че оригиналните `ACAD_PROXY_ENTITY` записи се
маркираха `unsupported_for_render`, а графиката извън строгия адаптер не
достигаше до drawing view. Разширяване само на layer allow-list не решаваше
проблема.

## 2. Възпроизвеждане върху rc42

Провереният файл е:

`39_83_shaibi_+21.48+24.78  do +24.78+28.08.dxf`

В rc42 са потвърдени 341 `ACAD_PROXY_ENTITY`, от които 335 в
`arm_shaibi`. В embedded proxy graphic има общо 3 803 графични примитива:

- 1 171 полилинии;
- 439 дъги;
- 708 окръжности;
- 1 485 текстови фрагмента.

Строгият инженерен adapter правилно връща 0 геометрии и 0 надписа за този
файл, защото няма доказан GA_X/GA_Y контекст. rc42 обаче не показваше тези
3 803 налични визуални примитива и не даваше предупреждение.

## 3. Отделен display-only канал

Добавен е общ, независим extractor за наличната proxy графика. Той:

- обхожда proxy обекти във всички слоеве;
- не изисква позиционен номер, инженерно доказан мащаб, свързан надпис или
  конкретно име на слой;
- използва `virtual_entities()` и копия с трансформация; не разпуква и не
  презаписва оригиналния DXF;
- записва резултата в отделното поле `display_entities`, а не в
  authoritative `entities`.

Всеки display запис носи `display_only=true`,
`engineering_eligible=false`, stable `proxy_source_key`, `display_id`,
оригинален proxy handle, layer, virtual index, component index и конкретен
instance path. Drawing view не добавя тези записи в `handle_to_record` или
`handle_to_item`, не ги прави избираеми и не ги подава към анализ/export.

## 4. Геометрия, текст и цвят

Поддържат се `LINE`, `LWPOLYLINE`, `POLYLINE`, `ARC`, `CIRCLE`,
`TEXT`, `MTEXT`, `ELLIPSE` и `SPLINE`.

- Планарните `+Z/-Z` OCS полилинии се сериализират в WCS със запазени точки,
  затвореност и коригиран знак на bulge. Viewer-ът изгражда истински дъгов
  сегмент, включително затварящия сегмент, вместо невярна хорда. При наклонен
  OCS bulge кривата се проектира като WCS cubic Bezier, а не като невярна XY
  окръжност или права отсечка.
- Планарните дъги се пазят като център, радиус и видим sweep. При наклонена
  равнина или мащабиране, което превръща окръжността в елипса, се използва
  `ELLIPSE` геометрия.
- Текстовете пазят insert/alignment point, височина, ротация, `halign`,
  `valign`, width factor и FIT/ALIGNED правилата. `MTEXT` пази attachment,
  ширина и text direction. Разпознатите proxy надписи се показват по
  оригиналните им отделни фрагменти, но остават една selectable инженерна
  анотация.
- SPLINE пази точните NURBS audit данни; за екрана се използва генерирана
  display polyline, никога control polygon като уж истинска крива.
- Цветът се разрешава от true-color/ACI/BYLAYER/BYBLOCK преди generic
  suppression и за strict engineering записите. Пазят се `source_rgb` и
  контрастният `display_rgb`; в r-10 ACI 5/6 и proxy text true-color вече не
  падат към общо тъмносиво.
- Видимостта е логическо AND на всички слоеве по INSERT веригата. Включването
  на вътрешен слой не показва елемент под скрит родител. `Fit to drawing`
  включва новата графика, когато е видима, и не се свива от скрити елементи.

## 5. Вложени блокове и стабилна идентичност

Extractor-ът обхожда `INSERT` и `MINSERT`, композира трансформациите на
всяко ниво и различава всяко конкретно вмъкване на една block definition.
Layer 0 и BYBLOCK наследяването следват instance контекста. Има maximum-depth
и cycle guard; грешка в един клон не прекратява останалия импорт.

Координатите се сериализират в WCS. Отрицателен OCS и огледален INSERT
коригират ориентацията на bulge, ARC, TEXT и ELLIPSE, така че графиката да не
се появява от противоположната страна.

Конкретният shaibi DXF няма вложени proxy instances, затова nested, MINSERT,
rotation/scale/reflection, tilted OCS projection и cyclic-reference сценарии
са проверени със синтетични DXF fixtures.

## 6. Недублиране и кеш

Strict adapter и generic display extractor използват общ stable source key.
Когато strict adapter вече е създал видима инженерна геометрия или точен
текстов фрагмент, същият virtual primitive се потиска в generic display
канала. Viewer-ът има втори защитен dedupe по source key/display ID.

При r-10 са намерени 2 712 proxy примитива: 1 221 вече са представени от
strict engineering records и са потиснати, а останалите 1 491 се показват
само визуално. Strict резултатът остава 198 геометрии и 130 анотации.

При повторно отваряне на копие на r-10 кешът се презаписва детерминирано:
1 491 уникални display ID, без натрупване. Data identity и byte съдържанието
на `approved_geometry_links.json`, `manual_corrections.json` и
`review_center_state.json` остават непроменени.

Стар кеш от rc42 ще бъде обогатен с новото `display_entities` поле при
първото отваряне. Това не губи решения, но променя хеша на extracted cache;
затова стар Final Control freeze трябва да се изпълни отново преди export.

## 7. Диагностика

Import summary вече отчита отделно:

- намерени proxy instances;
- намерени, показани и потиснати като дубликат примитиви;
- complete, partial, missing и unreadable proxy обекти;
- типове на намерените примитиви;
- неподдържаните типове;
- за всеки проблем: layer, proxy/INSERT идентификатор, instance path,
  reason code и конкретна причина.

Един повреден proxy или block branch се изолира. `complete` означава само, че
всички налични и поддържани графични данни са извлечени; липсващ payload или
неподдържан primitive се обявява изрично и не се замества с измислена
геометрия.

## 8. Реални резултати

### Shaibi DXF

- 1 513 authoritative engineering/modelspace records — непроменени;
- 341/341 proxy instances обработени;
- 3 803 намерени, 3 803 показани, 0 потиснати, 0 неподдържани;
- 341 complete, 0 partial, 0 missing, 0 unreadable;
- 3 803 уникални display ID;
- 0 display-only записа в engineering `entities`;
- 0 display записа с `engineering_eligible=true`;
- strict engineering adapter: 0 геометрии и 0 анотации — непроменено.

### r-10

- strict proxy база: 298 proxy, 198 геометрии, 130 свързани анотации;
- 2 712 намерени display primitives, 1 221 точни дубликата потиснати,
  1 491 показани;
- batch резултат: 132 actionable, 130 approved;
- количества: 2 539 общо actionable броя и 2 535 approved броя — непроменени.

### r-11

- 9/9 ръчни одобрения;
- 4 584 броя;
- 5 877.590902748 kg;
- същите 9 annotation handles и F001/F003/F006/F007 разпределение;
- 0 batch errors.

r-12 fixture не е намерен в достъпната база и не е отчетен подвеждащо като
успешен или пропуснат тест.

## 9. Автоматични и визуални проверки

- Отделни proxy graphics, renderer и integration тестове проверяват всички
  слоеве, дъги, bulge, текст, елипса, spline, nested INSERT/MINSERT,
  rotation/scale/reflection, negative и tilted OCS, cycle guard,
  липсваща/повредена графика, unsupported тип, strict r-10 цветове,
  TEXT/MTEXT alignment, ancestor-layer AND visibility, visible-only fit
  bounds, non-selectability и dedupe.
- Реалният shaibi DXF е част от теста, когато е наличен на потвърдения път.
- r-10 и r-11 имат реални quantity/decision regression проверки.
- Пълен release pytest в Qt offscreen режим: **1 128 passed, 11 skipped,
  0 failed** от 1 139 събрани; exit code 0 за 143.27 s.
- Създадени са Qt offscreen before/after и focused screenshots. В rc43 се виждат
  диагоналните детайли горе вляво и дългите пръчки под десните сечения;
  кривите участъци остават криви. Това е визуална проверка на реалния импорт,
  но не замества интерактивната Windows приемка.

## 10. Ограничения и Windows проверка

- Автоматичният UI тест е изпълнен с Qt `offscreen`; не е извършен
  интерактивен тест на реален Windows дисплей/видеокарта.
- Пакетът е установеният source Windows release с installer/start BAT
  файлове, а не предварително компилиран `.exe`; PyInstaller не е наличен в
  тази среда. `BUILD_EXE.bat` остава за отделен executable build след
  инсталация.
- Точността на unsupported proprietary primitive не се гадае. Ако ezdxf не
  може да го декодира, import report го посочва и останалият чертеж продължава.
- Произволният DXF oblique ъгъл на glyph не се възпроизвежда като точен shear;
  текущият renderer го приближава с italic шрифт. Текстът, позицията и размерът
  не се губят.

Кратките стъпки за реалната проверка са в `WINDOWS_ACCEPTANCE_rc43.md`.
