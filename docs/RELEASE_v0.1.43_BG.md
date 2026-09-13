# LION REBAR DESKTOP v0.1.43 TEST

## Обхват

Това е тесен тестов ъпдейт върху приетия Stage 2R R7. Не съдържа
`Dimension Resolver`, нови форми или общо разхлабване на matcher допуските.

Production промяната е само A1: заключеният контролен профил на `F009` не се
налага върху profile-less topology fallback-а. Самият заключен профил
`8 + 79 + 15 + 79 + 8 = 189 cm` остава наличен като контролна декларация.

## Заключена проследимост

- Stage 2R R7 commit: `b8632fa92445d06663ff36c816a95a371d13b5c1`;
- matcher SHA-256: `fe46be28af71d50a490298809ef8e8688abe371ce4563ae17d9c140e6ae5fe69`;
- Engine v0.3.6 SHA-256: `ebf0725f77b351f117d7e60f4329a8cc0c92dccf09115527965e234c20f341a8`.

## Приемателна база от R7

- candidate: `195 passed / 3 skipped / 0 failed`;
- export: `19 passed / 0 failed / 0 skipped`;
- measurement: `760 cases / 480 exact / 0 silent wrong / 444 flagged / 0 NONE`;
- invariance: `280/280`.
