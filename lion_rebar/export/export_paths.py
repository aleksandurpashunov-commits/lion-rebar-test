from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PairedExportPaths:
    excel: Path
    excel_trace: Path
    dxf: Path
    dxf_trace: Path
    temporary_excel: Path
    temporary_excel_trace: Path
    temporary_dxf: Path
    temporary_dxf_trace: Path

    @property
    def temporary_files(self) -> tuple[Path, ...]:
        return (
            self.temporary_excel,
            self.temporary_excel_trace,
            self.temporary_dxf,
            self.temporary_dxf_trace,
        )


def build_paired_export_paths(excel_path: Path, token: str) -> PairedExportPaths:
    """Build final and atomic temporary paths for one Excel + DXF export.

    Every temporary file is a sibling of its final destination. This is a hard
    requirement of the transactional bundle replacement and also guarantees
    that ``os.replace`` remains on the same filesystem on Windows.
    """

    excel = Path(excel_path)
    if excel.suffix.lower() != ".xlsx":
        excel = excel.with_suffix(".xlsx")
    parent = excel.parent
    safe_token = "".join(character for character in str(token) if character.isalnum())
    if not safe_token:
        raise ValueError("Липсва token за временните export файлове.")

    dxf = excel.with_name(excel.stem + "_ПОЗИЦИИ.dxf")
    excel_trace = excel.with_name(excel.stem + "_TRACEABILITY.json")
    dxf_trace = dxf.with_name(dxf.stem + "_TRACEABILITY.json")
    temporary_excel = parent / f".lion_rebar_{safe_token}_{excel.stem}.xlsx"
    temporary_dxf = parent / f".lion_rebar_{safe_token}_{dxf.stem}.dxf"
    temporary_excel_trace = temporary_excel.with_name(
        temporary_excel.stem + "_TRACEABILITY.json"
    )
    temporary_dxf_trace = temporary_dxf.with_name(
        temporary_dxf.stem + "_TRACEABILITY.json"
    )

    result = PairedExportPaths(
        excel=excel,
        excel_trace=excel_trace,
        dxf=dxf,
        dxf_trace=dxf_trace,
        temporary_excel=temporary_excel,
        temporary_excel_trace=temporary_excel_trace,
        temporary_dxf=temporary_dxf,
        temporary_dxf_trace=temporary_dxf_trace,
    )
    parents = {path.parent.resolve() for path in (
        result.excel,
        result.excel_trace,
        result.dxf,
        result.dxf_trace,
        *result.temporary_files,
    )}
    if len(parents) != 1:
        raise ValueError("Export файловете не са в една папка.")
    return result
