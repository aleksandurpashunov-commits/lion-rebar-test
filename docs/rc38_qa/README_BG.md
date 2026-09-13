# rc38 QA доказателства

- `R4_acceptance_report.json` — fail-closed R-4 acceptance с точните броячи,
  source guard, Final Control, Excel/DXF traces и build provenance.
- `R4_beams_rows_1_70.png` — представителен render на секционно подредения
  лист „греди“.
- `R4_verticals_rows_1_40.png` — представителен render на лист „вертикали“.
- `R4_total_rows_61_120.png` — представителен render на окрупнения лист
  „Общо“, включително F005/F007 контролните позиции.
- `UI_scale_100_metrics.json`, `UI_scale_125_metrics.json` и
  `UI_scale_150_metrics.json` — измерен Qt UI render контрол за F005/F006/F007
  и Review Center.

PNG файловете са read-only render на генерирания R-4 Excel. UI метриките са
от offscreen Qt среда; реалното Windows изобразяване на кирилица и native DPI
остава в операторския чеклист.
