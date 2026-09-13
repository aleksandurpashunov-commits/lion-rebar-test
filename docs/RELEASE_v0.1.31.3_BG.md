# LION REBAR DESKTOP v0.1.31.3 — Qt application bootstrap for MASTER rasterization

- Създава offscreen `QApplication` преди `QSvgRenderer`, когато export renderer-ът се тества извън стартираното desktop приложение.
- Не променя MASTER v0.3.1, формите, размерните правила или Excel оформлението.
- Изпълнява SVG raster smoke test в отделен subprocess с 35-секунден timeout, така че тестът да даде конкретна грешка вместо да блокира целия updater за 5 минути.
- Активната v0.1.30 остава непокътната, докато всички тестове не преминат.
