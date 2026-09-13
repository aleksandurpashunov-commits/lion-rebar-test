# Архитектура v0.1.0

```text
lion_rebar/
├── app.py                     # входна точка
├── config/                    # константи и заключени governance правила
├── dxf/
│   ├── importer.py            # ezdxf import и handles
│   ├── models.py              # DTO за DXF entities
│   └── text_parser.py         # първоначален parser за N/Ø/Ф означения
├── engine/
│   ├── models.py              # engine, форма, сегмент, правило, категория
│   ├── legacy_import.py       # HTML/XLSX/draft JSON import
│   ├── validation.py          # структурни и revision проверки
│   ├── test_runner.py         # контролни дължини
│   ├── repository.py          # drafts/published/current/rollback/audit
│   └── package_io.py          # .lionengine import/export
├── project/
│   ├── models.py
│   └── manager.py             # проектна папка и snapshots
├── ui/
│   ├── main_window.py
│   ├── drawing_view.py
│   ├── engine_studio.py
│   └── shape_preview.py
└── utils/
```

## Данни срещу код

`EnginePackage` е външен versioned data contract. Програмата не трябва да съдържа hard-coded F001–F009 geometry като единствен източник. Реалният MASTER се импортира и публикува в registry.

## Legacy compatibility

HTML importer-ът търси:

```javascript
const DEFAULT_LIBRARY={...};
```

и преобразува листовете:

- `ФОРМИ`;
- `СЕГМЕНТИ`;
- `ИЗЧЕРТАВАНЕ`;
- `ТЕСТОВЕ`;
- `НАСТРОЙКИ`;
- `РАЗМЕРИ`.

Оригиналният HTML се пази като SHA256 asset.

## Project pinning

Всеки проект пази:

- engine id;
- engine version;
- engine checksum;
- JSON snapshot в `project/master/`.

Това позволява възпроизвеждане на стар резултат.
