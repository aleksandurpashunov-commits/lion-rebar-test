# -*- coding: utf-8 -*-
"""Отклоненията: минала ли е през портата връзка с несъвпадаща дължина.

Това е проверката за ГРЕШНО приемане. Повече преминали позиции не е успех, ако
сред тях има връзка, чиято измерена дължина не отговаря на надписаната.

Допускът е добавката за огъване: до 2,1 cm разлика е в допуска на програмата
(``numeric_difference <= 2.1`` се ползва и в ``batch_geometry_report``). Всичко
над това трябва да е СПРЯНО.

Също проверява и вградената ведомост: DXF държи двоичните данни на ``OLE2FRAME``
като шестнайсетичен текст в група 310, затова ASCII търсене за ``Excel.Sheet`` не
я намира — търси се подписът на CFB ``D0CF11E0``.

Само чете.
"""
from __future__ import annotations

import argparse
import json
import pathlib


def _отчет(копие: str) -> pathlib.Path | None:
    папка = pathlib.Path(копие) / "output" / "04_REVIEW_REPORTS"
    ако = sorted(папка.rglob("*_geometry_batch.json")) if папка.is_dir() else []
    return ако[-1] if ако else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("obobshtenie", type=pathlib.Path)
    parser.add_argument("--dopusk", type=float, default=2.1)
    parser.add_argument("--dxf", type=pathlib.Path, default=None)
    args = parser.parse_args()

    д = json.loads(args.obobshtenie.read_text(encoding="utf-8"))
    отчет = _отчет(str(д.get("copy") or ""))
    редове = json.loads(отчет.read_text(encoding="utf-8")).get("rows") or []
    порта = д.get("gate") or {}
    спрени = {str(р.get("handle") or "") for р in порта.get("blocked_positions") or ()}
    print("порта: минали %s, спрени %d" % (порта.get("approved"), len(спрени)))

    извън = []
    for ред in редове:
        р = ред.get("numeric_difference")
        if р is None:
            continue
        if abs(float(р)) <= args.dopusk:
            continue
        ръчка = str(ред.get("annotation_handle") or "")
        извън.append({
            "надпис": ръчка,
            "текст": str(ред.get("annotation_text") or "")[:34],
            "разлика": round(abs(float(р)), 3),
            "обявена": ред.get("declared_length_cm"),
            "измерена": ред.get("effective_geometry_length_cm"),
            "слой": str(ред.get("candidate_layer") or "")[:22],
            "спряна": ръчка in спрени,
            "присъда": str(ред.get("matcher_disposition") or ""),
            "състояние": str(ред.get("batch_status") or "")[:24],
        })

    минали_извън = [и for и in извън if not и["спряна"]]
    print("\nредове с разлика над %.1f cm: %d | ОТ ТЯХ МИНАЛИ ПОРТАТА: %d"
          % (args.dopusk, len(извън), len(минали_извън)))
    for и in извън:
        print("   %-8s %-34s разлика=%-8s обявена=%-7s измерена=%-8s %s %s | %s"
              % (и["надпис"], и["текст"], и["разлика"], и["обявена"],
                 и["измерена"], "СПРЯНА" if и["спряна"] else "МИНАЛА",
                 и["присъда"], и["състояние"]))

    if args.dxf and args.dxf.is_file():
        текст = args.dxf.read_bytes()
        подпис = текст.upper().count(b"D0CF11E0")
        print("\nвградена ведомост: подпис CFB (D0CF11E0) се среща %d пъти" % подпис)
        if подпис:
            print("   -> двоичният блок е в чертежа; четенето му иска отделен "
                  "четец BIFF8 (няма xlrd/olefile в .venv)")
        else:
            print("   -> в ТОЗИ файл няма вградена електронна таблица; "
                  "независим еталон от чертежа НЕ е наличен")
    return 0 if not минали_извън else 2


if __name__ == "__main__":
    raise SystemExit(main())
