from __future__ import annotations

import json

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout

from lion_rebar.dxf.models import DxfImportSummary


class ImportReportDialog(QDialog):
    def __init__(self, summary: DxfImportSummary, parent=None, *, layer_report: dict | None = None):
        super().__init__(parent)
        self.setWindowTitle("DXF импортен отчет")
        self.resize(760, 680)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        payload = {"import_summary": summary.to_dict()}
        if isinstance(layer_report, dict):
            payload["layer_classification"] = {
                "summary": dict(layer_report.get("summary") or {}),
                "layers": list(layer_report.get("layers") or []),
            }
        text.setPlainText(json.dumps(payload, ensure_ascii=False, indent=2))
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout = QVBoxLayout(self)
        layout.addWidget(text)
        layout.addWidget(buttons)
