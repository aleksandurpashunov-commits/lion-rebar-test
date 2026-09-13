# LION REBAR DESKTOP v0.1.44.20-rc31

## Поправен реален дефект

В `ръчно-002` пакетната производствена порта правилно одобри 13 F006
позиции чрез доказаните размери от именуваните `BJ-FI-L` блокови атрибути.
След решаването на първоначалните 3 инженерни позиции Final Integrity ги
отне и ги показа като 13 нови инженерни казуса.

Причината беше второ, независимо и по-тясно правило във Final Integrity.
То гледаше само текстовия `batch_status` и не познаваше deterministic
изключението на реалната производствена порта.

rc31 въвежда една shared AUTO policy за:

- каноничните batch статуси;
- matcher disposition;
- deterministic block source с доказани производствени размери;
- audit mode на всяко AUTO одобрение.

Final Integrity допълнително проверява точния SHA-256 на текущия batch
отчет, името му, source DXF hash и задължителния engine/matcher/rules
provenance. Обикновен MANUAL ред без deterministic доказателство остава
блокиран — безопасността не е разхлабена.

## Възстановяване на ръчно-002

`tools/recover_auto_gate_rc31.py` първо работи в dry-run режим. При `--apply`
той:

1. създава recovery backup на текущите working файлове;
2. изпълнява отново официалната AUTO производствена порта;
3. запазва всичките 3 инженерни и 3 общи USER решения;
4. стартира Final Integrity и изисква 159/159, 0 нерешени, 0 дублирания и
   0 identity грешки.

Инструментът отказва всякакъв запис, ако очакваните 13 връзки не съвпадат
точно по current project/source/run identity, report hash, candidate geometry
и gate eligibility.

## Проверка

- shared policy regression тестове за `СИЛЕН`, MANUAL и deterministic F006;
- Final Integrity regression за валидна deterministic AUTO връзка;
- негативен regression за променен batch report SHA-256;
- целият pytest suite: PASS;
- реален recovery върху `ръчно-002`: 159/159, 0 нерешени, 0 дублирания,
  0 identity грешки и VALID freeze с 8/8 проверени хеша;
- recovery backup:
  `working/recovery_backups/20260829_160620_rc31_auto_gate`.
