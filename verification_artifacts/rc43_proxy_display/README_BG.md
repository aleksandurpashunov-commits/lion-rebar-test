# rc43 proxy display — визуални доказателства

Версия: `LION REBAR v0.1.44.20-rc43`  
Дата: 08.09.2026 г.  
Режим: Qt `offscreen`, чрез реалния importer и `build_scene()`; това не е
интерактивна Windows приемка.

Входен DXF:
`39_83_shaibi_+21.48+24.78  do +24.78+28.08.dxf`

SHA-256:
`6f04213d033cb4f70a4ffbff56116fc3332ba452710aaf2754c76ac26068651e`

Файлове:

- `before_after_main_sheet.png` — общо сравнение преди/след;
- `rc42_proxy_missing.png` — rc42 без display-only proxy канала;
- `rc43_proxy_visible.png` — rc43 с извлечената proxy графика;
- `rc43_final_scene.png` — финално offscreen изобразяване на целия scene;
- `requested_regions_rc43_final_zoom.png` — заявените области: диагоналните
  детайли горе вляво и дългите пръчки под десните сечения.

Снимките доказват визуалния резултат за конкретния shaibi DXF. Synthetic
pytest fixtures, а не тези снимки, доказват mirrored/negative OCS, tilted OCS,
parent-layer visibility и strict r-10 color сценарии.
