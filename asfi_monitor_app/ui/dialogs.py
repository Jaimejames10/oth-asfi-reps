"""Dialogos Tkinter para reglas y catalogo de reportes."""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from .formatters import (
    DIAS_SEMANA_OPCIONES,
    _days_to_text,
    _optional_int,
    _parse_days,
    _parse_weekday,
    _weekday_label,
)
from asfi_monitor_app.storage import api as reportes_db


class RuleDialog(tk.Toplevel):
    def __init__(
        self, parent: tk.Misc, rule: Optional[dict] = None, weekly: bool = False
    ):
        super().__init__(parent)
        self.title("Regla de calendario")
        self.resizable(False, False)
        self.result = None
        rule = rule or {}
        self.weekly = weekly or reportes_db.is_weekly_rule(rule)

        self.cutoff_rule = tk.StringVar(
            value=("SEMANAL" if self.weekly else rule.get("regla_fecha_corte", "AYER"))
        )
        self.cutoff_weekday = tk.StringVar(
            value=_weekday_label(rule.get("dia_semana_corte"), 5) if self.weekly else (
                "" if rule.get("dia_semana_corte") is None else str(rule["dia_semana_corte"])
            )
        )
        self.cutoff_month_day = tk.StringVar(
            value="" if rule.get("dia_mes_corte") is None else str(rule["dia_mes_corte"])
        )
        self.send_days = tk.StringVar(
            value="" if self.weekly else _days_to_text(rule.get("dias_envio", []))
        )
        self.deadline_time = tk.StringVar(
            value="12:00" if self.weekly else (rule.get("hora_limite") or "")
        )
        self.occurrences = tk.StringVar(value=str(rule.get("ocurrencias_requeridas", 1)))
        self.grace_days = tk.StringVar(
            value="" if rule.get("dias_plazo") is None else str(rule["dias_plazo"])
        )
        self.grace_type = tk.StringVar(value=rule.get("tipo_plazo") or "calendario")
        self.frequency_months = tk.StringVar(value=str(rule.get("frecuencia_meses", 1)))
        self.anchor_month = tk.StringVar(
            value="" if rule.get("mes_ancla") is None else str(rule["mes_ancla"])
        )

        frame = ttk.Frame(self, padding=12)
        frame.grid(sticky="nsew")
        fields = [
            ("cutoff_rule", "Regla fecha corte", self.cutoff_rule),
            ("weekday", "Día de corte semanal", self.cutoff_weekday),
            ("month_day", "Día corte mes", self.cutoff_month_day),
            (
                "send_days",
                "Ventana de envío" if self.weekly else "Días permitidos envío (1-7)",
                self.send_days,
            ),
            (
                "deadline_time",
                "Límite proyecto" if self.weekly else "Hora límite (HH:MM)",
                self.deadline_time,
            ),
            ("occurrences", "Envíos requeridos (1, 2, 3...)", self.occurrences),
            ("grace_days", "Días de plazo", self.grace_days),
            ("grace_type", "Tipo de plazo", self.grace_type),
            ("frequency", "Frecuencia en meses", self.frequency_months),
            ("anchor_month", "Mes ancla (1-12)", self.anchor_month),
        ]
        for row, (key, label, variable) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            if key == "cutoff_rule":
                widget = ttk.Combobox(
                    frame,
                    textvariable=variable,
                    values=("AYER", "SEMANAL", "VIERNES", "SEMANA", "FIN_MES", "FIN_PERIODO"),
                    state="readonly",
                    width=20,
                )
            elif key == "weekday":
                widget = ttk.Combobox(
                    frame,
                    textvariable=variable,
                    values=DIAS_SEMANA_OPCIONES,
                    state="readonly" if self.weekly else "normal",
                    width=20,
                )
            elif self.weekly and key == "send_days":
                widget = ttk.Label(frame, text="Día posterior al corte", width=23, anchor="w")
            elif self.weekly and key == "deadline_time":
                widget = ttk.Label(frame, text="Lunes a las 12:00 (automático)", width=23, anchor="w")
            elif self.weekly and key in {"grace_days", "grace_type"}:
                widget = ttk.Label(frame, text="No aplica a reportes semanales", width=23, anchor="w")
            elif key == "grace_type":
                widget = ttk.Combobox(
                    frame,
                    textvariable=variable,
                    values=("calendario", "habil"),
                    state="readonly",
                    width=20,
                )
            else:
                widget = ttk.Entry(frame, textvariable=variable, width=23)
            widget.grid(row=row, column=1, sticky="ew", pady=3)

        ttk.Label(
            frame,
            text=(
                "El reporte se busca por su fecha de corte y vence el lunes a las 12:00. "
                "1 = lunes ... 7 = domingo."
                if self.weekly
                else "1 = lunes ... 7 = domingo. Envíos requeridos indica cuántas ocurrencias deben llegar."
            ),
            foreground="#555555",
        ).grid(row=len(fields), column=0, columnspan=2, sticky="w", pady=(6, 10))
        buttons = ttk.Frame(frame)
        buttons.grid(row=len(fields) + 1, column=0, columnspan=2, sticky="e")
        ttk.Button(buttons, text="Cancelar", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Aceptar", command=self._accept).pack(side="right")
        self.bind("<Return>", lambda _event: self._accept())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.transient(parent)
        self.grab_set()
        self.focus_force()

    def _accept(self) -> None:
        try:
            hour = self.deadline_time.get().strip() or None
            reportes_db.parse_time(hour)
            cutoff_weekday = _parse_weekday(self.cutoff_weekday.get())
            if cutoff_weekday is not None and cutoff_weekday > 7:
                raise ValueError("Día corte semana debe estar entre 1 y 7")
            cutoff_month_day = _optional_int(self.cutoff_month_day.get(), "Día corte mes", 1)
            if cutoff_month_day is not None and cutoff_month_day > 31:
                raise ValueError("Día corte mes debe estar entre 1 y 31")
            anchor_month = _optional_int(self.anchor_month.get(), "Mes ancla", 1)
            if anchor_month is not None and anchor_month > 12:
                raise ValueError("Mes ancla debe estar entre 1 y 12")
            occurrences = _optional_int(self.occurrences.get(), "Ocurrencias", 1) or 1
            frequency = _optional_int(self.frequency_months.get(), "Frecuencia", 1) or 1
            grace_days = _optional_int(self.grace_days.get(), "Días de plazo", 0)
            grace_type = self.grace_type.get() or None
            days = _parse_days(self.send_days.get())
            if self.weekly:
                cutoff_weekday = cutoff_weekday or 5
                hour = reportes_db.WEEKLY_DEADLINE_TIME
                days = []
                grace_days = None
                grace_type = None
            self.result = {
                "regla_fecha_corte": "SEMANAL" if self.weekly else self.cutoff_rule.get(),
                "dia_semana_corte": cutoff_weekday,
                "dia_mes_corte": cutoff_month_day,
                "frecuencia_meses": frequency,
                "dias_envio": days,
                "ocurrencias_requeridas": occurrences,
                "hora_limite": hour,
                "dias_plazo": grace_days,
                "tipo_plazo": grace_type,
                "mes_ancla": anchor_month,
            }
        except ValueError as exc:
            messagebox.showerror("Regla inválida", str(exc), parent=self)
            return
        self.destroy()


class ReportDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, report: Optional[dict] = None):
        super().__init__(parent)
        self.title("Editar reporte" if report else "Nuevo reporte")
        self.resizable(False, True)
        self.result = None
        report = report or {}

        self.report_id = report.get("id")
        self.code = tk.StringVar(value=report.get("codigo", ""))
        self.name = tk.StringVar(value=report.get("nombre", ""))
        self.period = tk.StringVar(value=report.get("tipo_periodo", "diario"))
        self.description = tk.StringVar(value=report.get("descripcion", ""))
        self.active = tk.BooleanVar(value=bool(report.get("activo", True)))
        self.validation = tk.BooleanVar(value=bool(report.get("validacion_activa", True)))
        aliases = report.get("aliases", [])
        self.aliases = tk.StringVar(value=", ".join(alias for alias in aliases if alias != report.get("nombre")))
        self.rules = [dict(rule) for rule in report.get("reglas", [])]

        frame = ttk.Frame(self, padding=12)
        frame.grid(sticky="nsew")
        fields = [
            ("Código", self.code),
            ("Nombre mostrado en SCIP", self.name),
            ("Tipo de período", self.period),
            ("Descripción", self.description),
            ("Alias separados por coma", self.aliases),
        ]
        for row, (label, variable) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            if label == "Tipo de período":
                widget = ttk.Combobox(
                    frame,
                    textvariable=variable,
                    values=("diario", "semanal", "mensual", "trimestral", "semestral", "anual", "otro"),
                    state="readonly",
                    width=30,
                )
            else:
                widget = ttk.Entry(frame, textvariable=variable, width=34)
            widget.grid(row=row, column=1, sticky="ew", pady=3)

        ttk.Checkbutton(frame, text="Reporte activo", variable=self.active).grid(
            row=len(fields), column=0, sticky="w", pady=5
        )
        ttk.Checkbutton(frame, text="Validación activa", variable=self.validation).grid(
            row=len(fields), column=1, sticky="w", pady=5
        )
        ttk.Label(frame, text="Reglas de calendario").grid(
            row=len(fields) + 1, column=0, columnspan=2, sticky="w", pady=(8, 3)
        )
        self.rules_tree = ttk.Treeview(
            frame,
            columns=("rule", "cutoff", "send", "time", "occ", "grace", "freq"),
            show="headings",
            height=7,
        )
        headings = {
            "rule": "Regla",
            "cutoff": "Día corte",
            "send": "Días envío",
            "time": "Hora",
            "occ": "Ocurr.",
            "grace": "Plazo",
            "freq": "Frecuencia",
        }
        for key, heading in headings.items():
            self.rules_tree.heading(key, text=heading)
            self.rules_tree.column(key, width=90 if key != "rule" else 110, anchor="center")
        self.rules_tree.grid(row=len(fields) + 2, column=0, columnspan=2, sticky="nsew")
        self.rules_tree.bind("<Double-1>", lambda _event: self._edit_rule())
        self._refresh_rules()

        rule_buttons = ttk.Frame(frame)
        rule_buttons.grid(row=len(fields) + 3, column=0, columnspan=2, sticky="e", pady=5)
        ttk.Button(rule_buttons, text="Agregar regla", command=self._add_rule).pack(side="left", padx=3)
        ttk.Button(rule_buttons, text="Editar regla", command=self._edit_rule).pack(side="left", padx=3)
        ttk.Button(rule_buttons, text="Quitar regla", command=self._remove_rule).pack(side="left", padx=3)
        buttons = ttk.Frame(frame)
        buttons.grid(row=len(fields) + 4, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="Cancelar", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="Guardar", command=self._accept).pack(side="right")
        self.bind("<Escape>", lambda _event: self.destroy())
        self.transient(parent)
        self.grab_set()
        self.focus_force()

    def _rule_summary(self, rule: dict) -> tuple:
        weekly = reportes_db.is_weekly_rule(rule)
        cutoff = (
            _weekday_label(rule.get("dia_semana_corte"))
            if weekly
            else rule.get("dia_semana_corte") or rule.get("dia_mes_corte") or "-"
        )
        grace = rule.get("dias_plazo")
        grace_text = "Lun 12:00" if weekly else ("-" if grace is None else str(grace))
        return (
            "SEMANAL" if weekly else rule.get("regla_fecha_corte", ""),
            str(cutoff),
            "DÍA SIGUIENTE" if weekly else _days_to_text(rule.get("dias_envio", [])) or "-",
            "Lunes 12:00" if weekly else rule.get("hora_limite") or "-",
            str(rule.get("ocurrencias_requeridas", 1)),
            grace_text,
            str(rule.get("frecuencia_meses", 1)),
        )

    def _refresh_rules(self) -> None:
        for item in self.rules_tree.get_children():
            self.rules_tree.delete(item)
        for index, rule in enumerate(self.rules):
            self.rules_tree.insert("", "end", iid=str(index), values=self._rule_summary(rule))

    def _selected_rule_index(self) -> Optional[int]:
        selected = self.rules_tree.selection()
        return int(selected[0]) if selected else None

    def _add_rule(self) -> None:
        dialog = RuleDialog(self, weekly=self.period.get().strip().lower() == "semanal")
        self.wait_window(dialog)
        if dialog.result is not None:
            self.rules.append(dialog.result)
            self._refresh_rules()

    def _edit_rule(self) -> None:
        index = self._selected_rule_index()
        if index is None:
            messagebox.showwarning("Regla", "Seleccione una regla primero.", parent=self)
            return
        dialog = RuleDialog(
            self,
            self.rules[index],
            weekly=self.period.get().strip().lower() == "semanal",
        )
        self.wait_window(dialog)
        if dialog.result is not None:
            self.rules[index] = dialog.result
            self._refresh_rules()

    def _remove_rule(self) -> None:
        index = self._selected_rule_index()
        if index is None:
            messagebox.showwarning("Regla", "Seleccione una regla primero.", parent=self)
            return
        del self.rules[index]
        self._refresh_rules()

    def _accept(self) -> None:
        code = self.code.get().strip()
        name = self.name.get().strip()
        if not code or not name:
            messagebox.showerror("Reporte inválido", "Código y nombre son obligatorios.", parent=self)
            return
        if not self.rules:
            messagebox.showerror("Reporte inválido", "Agregue al menos una regla de calendario.", parent=self)
            return
        self.result = {
            "id": self.report_id,
            "codigo": code,
            "nombre": name,
            "tipo_periodo": self.period.get().strip().lower(),
            "descripcion": self.description.get().strip(),
            "activo": self.active.get(),
            "validacion_activa": self.validation.get(),
            "aliases": [alias.strip() for alias in self.aliases.get().split(",") if alias.strip()],
            "reglas": self.rules,
        }
        self.destroy()
