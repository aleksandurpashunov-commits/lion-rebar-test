# LION REBAR v0.1.44.16-recovery.6.1 — Local-first END SESSION fix

## Реален дефект

При Windows smoke test бутонът `Край на сесията` върна:

```text
File is not a zip file
```

Session Database запази данните, но сесията остана активна. Цената на дефекта е висока: един завършен работен ден не трябва да зависи от състоянието на cloud filesystem или от стар/частичен ZIP.

## Поправка

1. Всички файлове на Learning Pack се събират като bytes.
2. ZIP-ът се изгражда в паметта.
3. Проверяват се `manifest.json`, размерът и SHA256 на всеки деклариран файл.
4. Само проверени ZIP bytes се записват атомарно в `%LOCALAPPDATA%\LionBuild\LionRebarDesktop\session_core\learning_packs`.
5. `LATEST` се създава от същите проверени bytes; стар повреден файл се заменя.
6. При грешка в основната локална папка се прави втори опит в `%TEMP%\LionBuild\LionRebarDesktop\learning_pack_recovery`.
7. Едва след наличен проверен локален ZIP се прави best-effort копие към `06_LEARNING_PACKS` в cloud.
8. Cloud грешката се показва като предупреждение, но сесията се маркира `ENDED`.

## Съвместимост

Recovery 6.1 използва същата Session Database и `current_session.json`. Активната сесия от Recovery 6 се възстановява със същия Session ID, проектен брой и решения.

## Непроменени компоненти

- Engine: `0.3.13`;
- Matcher и Matcher SHA;
- F003/F005/F006;
- Local Detail Resolver;
- Beam Ledger;
- AUTO lifecycle;
- производствени допуски.

## Regression gate

- Learning Pack + Session Database tests: 14/14 PASS;
- повреден `LATEST` се заменя: PASS;
- cloud publication failure не блокира END SESSION: PASS;
- primary local ZIP failure → emergency local fallback → session ENDED: PASS;
- ZIP manifest/size/SHA verification: PASS.
- Пълен suite: 358 PASS, 12 SKIP, 1 environment-blocked/deselected.
- Реален smoke с `Греди тест - 003.dxf`, 16 MB batch report и предварително повреден cloud `LATEST`: PASS; локален и cloud ZIP са отворени и проверени по manifest/SHA.
