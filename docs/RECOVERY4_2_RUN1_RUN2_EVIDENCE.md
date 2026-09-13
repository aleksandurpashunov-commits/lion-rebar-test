# Recovery.4.2 — Run 1 → Run 2 lifecycle evidence

Реалният `original.dxf` е обработен два последователни пъти с едни и същи
Engine/Matcher/Rules и без потребителски промени между пробезите.

| Показател | Run 1 | Run 2 |
|---|---:|---:|
| Обработени надписи | 315 | 315 |
| Реални позиции | 150 | 150 |
| Позиции с кандидат | 150 | 150 |
| Ръчни одобрения, пропуснати | 0 | 0 |
| Силни по геометрия | 116 | 116 |
| Силни F006 | 32 | 32 |
| За ръчен преглед | 2 | 2 |
| AUTO gate passed | 134 | 134 |
| AUTO links written/refreshed | 134 | 134 |
| AUTO blocked | 16 | 16 |

Всичките 150 actionable реда имат идентични `batch_status`, MASTER, candidate
handles, matcher disposition и F006 production status в Run 1 и Run 2.
Всичките 134 предишни AUTO връзки са преизчислени; 134/134 съвпадат по
геометрия, но нито една не е превърната в ръчно одобрение.

- DXF SHA256: `66da0575ef0d4055f7fcd1e36519e7e0ca9a0de9a6ac47ad1df5f6ac645723ee`
- Engine bundle SHA256: `4a0e3efe5445bcbf7dd818a64a442e28abf5d4bdabbcaecebbc2c8a617a41f93`
- Matcher SHA256: `05210e71ba6dc68c4d98665194cece4a11b3ce15ea684680e070e1f9ac2f024b`

Пълните машинни числа са в `RECOVERY4_2_RUN1_RUN2_EVIDENCE.json`.
