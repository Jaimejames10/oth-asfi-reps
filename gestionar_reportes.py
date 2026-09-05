"""Panel de administración de ASFI Monitor: dashboard con menú lateral.

Secciones:
  - Estado del día: obligaciones del período vigente por reporte.
  - En cola: obligaciones de fechas anteriores que aún no se envían.
  - Catálogo de reportes: altas, bajas y reglas de calendario.
  - Credenciales: acceso a ASFI/SCIP protegido con DPAPI.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from datetime import datetime
from pathlib import Path
import sqlite3
import threading
from typing import Optional

import reportes_db


COLOR_SIDEBAR = "#1e293b"
COLOR_SIDEBAR_ACTIVE = "#334155"
COLOR_SIDEBAR_TEXT = "#cbd5e1"
COLOR_SIDEBAR_TITLE = "#f8fafc"
COLOR_BG = "#f1f5f9"
COLOR_CARD = "#ffffff"
COLOR_BORDER = "#e2e8f0"
COLOR_TEXT = "#0f172a"
COLOR_MUTED = "#64748b"
COLOR_ACCENT = "#2563eb"
COLOR_OK = "#16a34a"
COLOR_WARN = "#d97706"
COLOR_ERROR = "#dc2626"
COLOR_INFO = "#0284c7"
FONT = "Segoe UI"

ESTADO_COLORS = {
    "EXITOSO": COLOR_OK,
    "ABIERTO": COLOR_INFO,
    "PENDIENTE": COLOR_WARN,
    "ERROR": COLOR_ERROR,
    "FALTANTE": COLOR_ERROR,
    "CONFIGURAR": COLOR_MUTED,
}


def _optional_int(value: str, field: str, minimum: int = 0) -> Optional[int]:
    value = value.strip()
    if not value:
        return None
    try:
        result = int(value)
    except ValueError as exc:
        raise ValueError(f"{field} debe ser un número entero") from exc
    if result < minimum:
        raise ValueError(f"{field} debe ser mayor o igual a {minimum}")
    return result


def _days_to_text(days: list) -> str:
    return ",".join(str(day) for day in days)


def _parse_days(value: str) -> list[int]:
    if not value.strip():
        return []
    try:
        days = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise ValueError("Los días deben ser números del 1 al 7 separados por comas") from exc
    if any(day < 1 or day > 7 for day in days):
        raise ValueError("Los días deben estar entre 1 (lunes) y 7 (domingo)")
    return sorted(set(days))


def _display_date(value: Optional[str]) -> str:
    parsed = reportes_db.parse_date(value)
    return reportes_db.format_asfi_date(parsed) if parsed else "-"


def _display_datetime(value: Optional[str]) -> str:
    if not value:
        return "-"
    try:
        return datetime.fromisoformat(value).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(value).replace("T", " ")[:16]


class RuleDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, rule: Optional[dict] = None):
        super().__init__(parent)
        self.title("Regla de calendario")
        self.resizable(False, False)
        self.result = None
        rule = rule or {}

        self.cutoff_rule = tk.StringVar(value=rule.get("regla_fecha_corte", "AYER"))
        self.cutoff_weekday = tk.StringVar(
            value="" if rule.get("dia_semana_corte") is None else str(rule["dia_semana_corte"])
        )
        self.cutoff_month_day = tk.StringVar(
            value="" if rule.get("dia_mes_corte") is None else str(rule["dia_mes_corte"])
        )
        self.send_days = tk.StringVar(value=_days_to_text(rule.get("dias_envio", [])))
        self.deadline_time = tk.StringVar(value=rule.get("hora_limite") or "")
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
            ("Regla fecha corte", self.cutoff_rule),
            ("Día corte semana (1-7)", self.cutoff_weekday),
            ("Día corte mes", self.cutoff_month_day),
            ("Días permitidos envío (1-7)", self.send_days),
            ("Hora límite (HH:MM)", self.deadline_time),
            ("Ocurrencias requeridas", self.occurrences),
            ("Días de plazo", self.grace_days),
            ("Tipo de plazo", self.grace_type),
            ("Frecuencia en meses", self.frequency_months),
            ("Mes ancla (1-12)", self.anchor_month),
        ]
        for row, (label, variable) in enumerate(fields):
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=3)
            if label == "Regla fecha corte":
                widget = ttk.Combobox(
                    frame,
                    textvariable=variable,
                    values=("AYER", "VIERNES", "FIN_MES", "FIN_PERIODO"),
                    state="readonly",
                    width=20,
                )
            elif label == "Tipo de plazo":
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
            text="1 = lunes ... 7 = domingo. Deje vacíos los campos que no apliquen.",
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
            cutoff_weekday = _optional_int(self.cutoff_weekday.get(), "Día corte semana", 1)
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
            days = _parse_days(self.send_days.get())
            self.result = {
                "regla_fecha_corte": self.cutoff_rule.get(),
                "dia_semana_corte": cutoff_weekday,
                "dia_mes_corte": cutoff_month_day,
                "frecuencia_meses": frequency,
                "dias_envio": days,
                "ocurrencias_requeridas": occurrences,
                "hora_limite": hour,
                "dias_plazo": grace_days,
                "tipo_plazo": self.grace_type.get() or None,
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
        cutoff = rule.get("dia_semana_corte") or rule.get("dia_mes_corte") or "-"
        grace = rule.get("dias_plazo")
        grace_text = "-" if grace is None else str(grace)
        return (
            rule.get("regla_fecha_corte", ""),
            str(cutoff),
            _days_to_text(rule.get("dias_envio", [])) or "-",
            rule.get("hora_limite") or "-",
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
        if not selected:
            return None
        return int(selected[0])

    def _add_rule(self) -> None:
        dialog = RuleDialog(self)
        self.wait_window(dialog)
        if dialog.result is not None:
            self.rules.append(dialog.result)
            self._refresh_rules()

    def _edit_rule(self) -> None:
        index = self._selected_rule_index()
        if index is None:
            messagebox.showwarning("Regla", "Seleccione una regla primero.", parent=self)
            return
        dialog = RuleDialog(self, self.rules[index])
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


class Dashboard(tk.Tk):
    SECTIONS = (
        ("estado", "Estado del día"),
        ("cola", "Vencidos sin enviar"),
        ("reportes", "Catálogo de reportes"),
        ("credenciales", "Credenciales"),
    )

    def __init__(self, db_path: Path):
        super().__init__()
        self.title("ASFI/SCIP Monitor - Panel de administración")
        self.geometry("1150x700")
        self.minsize(1000, 620)
        self.configure(bg=COLOR_BG)
        self.db_path = db_path
        self.conn = reportes_db.connect(db_path)
        self.items: dict[str, dict] = {}
        self.nav_buttons: dict[str, tk.Button] = {}
        self.pages: dict[str, tk.Frame] = {}
        self.protocol("WM_DELETE_WINDOW", self._close)
        try:
            reportes_db.ensure_obligations(self.conn)
        except Exception:
            pass
        self._build_style()
        self._build_sidebar()
        self._build_pages()
        self._show_section("estado")

    # ---------- construcción general ----------

    def _build_style(self) -> None:
        style = ttk.Style(self)
        for theme in ("vista", "clam"):
            try:
                style.theme_use(theme)
                break
            except tk.TclError:
                continue
        style.configure("Dash.Treeview", rowheight=28, font=(FONT, 10), background=COLOR_CARD)
        style.configure(
            "Dash.Treeview.Heading",
            font=(FONT, 9, "bold"),
            background=COLOR_BG,
            foreground=COLOR_TEXT,
            relief="flat",
        )
        style.configure("Dash.TButton", padding=(10, 5))
        style.configure("Dash.TLabelframe", background=COLOR_CARD, borderwidth=0)
        style.configure("Dash.TLabelframe.Label", font=(FONT, 10, "bold"), background=COLOR_CARD)
        style.configure("Dash.TFrame", background=COLOR_CARD)
        style.configure("Dash.TLabel", background=COLOR_CARD, foreground=COLOR_TEXT)

    def _build_sidebar(self) -> None:
        sidebar = tk.Frame(self, bg=COLOR_SIDEBAR, width=230)
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        tk.Label(
            sidebar,
            text="ASFI / SCIP Monitor",
            bg=COLOR_SIDEBAR,
            fg=COLOR_SIDEBAR_TITLE,
            font=(FONT, 13, "bold"),
            anchor="w",
        ).pack(fill="x", padx=18, pady=(20, 2))
        tk.Label(
            sidebar,
            text="Panel de administración",
            bg=COLOR_SIDEBAR,
            fg=COLOR_SIDEBAR_TEXT,
            font=(FONT, 9),
            anchor="w",
        ).pack(fill="x", padx=18, pady=(0, 18))

        nav = tk.Frame(sidebar, bg=COLOR_SIDEBAR)
        nav.pack(fill="x", padx=10)
        for key, text in self.SECTIONS:
            button = tk.Button(
                nav,
                text=text,
                anchor="w",
                font=(FONT, 10),
                bg=COLOR_SIDEBAR,
                fg=COLOR_SIDEBAR_TEXT,
                activebackground=COLOR_SIDEBAR_ACTIVE,
                activeforeground=COLOR_SIDEBAR_TITLE,
                relief="flat",
                bd=0,
                padx=14,
                pady=9,
                cursor="hand2",
                command=lambda k=key: self._show_section(k),
            )
            button.pack(fill="x", pady=1)
            self.nav_buttons[key] = button

        footer = tk.Frame(sidebar, bg=COLOR_SIDEBAR)
        footer.pack(side="bottom", fill="x", padx=18, pady=14)
        tk.Label(
            footer,
            text="Base de datos",
            bg=COLOR_SIDEBAR,
            fg=COLOR_MUTED,
            font=(FONT, 8),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            footer,
            text=self.db_path.name,
            bg=COLOR_SIDEBAR,
            fg=COLOR_SIDEBAR_TEXT,
            font=(FONT, 9),
            anchor="w",
        ).pack(fill="x")

    def _build_pages(self) -> None:
        content = tk.Frame(self, bg=COLOR_BG)
        content.pack(side="left", fill="both", expand=True)
        content.rowconfigure(0, weight=1)
        content.columnconfigure(0, weight=1)
        builders = {
            "estado": self._build_page_estado,
            "cola": self._build_page_cola,
            "reportes": self._build_page_reportes,
            "credenciales": self._build_page_credenciales,
        }
        for key, builder in builders.items():
            page = tk.Frame(content, bg=COLOR_BG)
            page.grid(row=0, column=0, sticky="nsew")
            builder(page)
            self.pages[key] = page

    def _page_header(self, parent: tk.Frame, title: str, subtitle: str) -> tk.Frame:
        header = tk.Frame(parent, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
        header.pack(fill="x", padx=18, pady=(16, 10))
        inner = tk.Frame(header, bg=COLOR_CARD)
        inner.pack(fill="x", padx=16, pady=12)
        tk.Label(
            inner, text=title, bg=COLOR_CARD, fg=COLOR_TEXT, font=(FONT, 14, "bold"), anchor="w"
        ).pack(side="left")
        tk.Label(
            inner, text=subtitle, bg=COLOR_CARD, fg=COLOR_MUTED, font=(FONT, 9), anchor="w"
        ).pack(side="left", padx=(12, 0), pady=(3, 0))
        return inner

    def _table(self, parent: tk.Frame, columns: tuple, headings: dict, widths: dict, height: int = 16) -> ttk.Treeview:
        wrapper = tk.Frame(parent, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
        wrapper.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        tree = ttk.Treeview(
            wrapper, columns=columns, show="headings", selectmode="browse", style="Dash.Treeview", height=height
        )
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor="w" if column in ("nombre",) else "center")
        scrollbar = ttk.Scrollbar(wrapper, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        wrapper.rowconfigure(0, weight=1)
        wrapper.columnconfigure(0, weight=1)
        for estado, color in ESTADO_COLORS.items():
            tree.tag_configure(estado.lower(), foreground=color)
        return tree

    def _show_section(self, key: str) -> None:
        for section, button in self.nav_buttons.items():
            if section == key:
                button.configure(bg=COLOR_SIDEBAR_ACTIVE, fg=COLOR_SIDEBAR_TITLE)
            else:
                button.configure(bg=COLOR_SIDEBAR, fg=COLOR_SIDEBAR_TEXT)
        refreshers = {
            "estado": self._refresh_estado,
            "cola": self._refresh_cola,
            "reportes": self._refresh_reportes,
            "credenciales": self._load_credentials,
        }
        self.pages[key].tkraise()
        refreshers[key]()

    # ---------- página: estado del día ----------

    def _build_page_estado(self, parent: tk.Frame) -> None:
        header = self._page_header(
            parent,
            "Estado de los reportes",
            "Período vigente por regla de calendario",
        )
        self.estado_refresh_button = ttk.Button(
            header,
            text="Actualizar",
            style="Dash.TButton",
            command=lambda: self._refresh_estado(revisar_asfi=True),
        )
        self.estado_refresh_button.pack(side="right")
        self.estado_progress = ttk.Progressbar(header, mode="indeterminate", length=110)
        self.estado_progress.pack(side="right", padx=(0, 8))
        self.estado_loading = tk.StringVar(value="")
        tk.Label(
            header,
            textvariable=self.estado_loading,
            bg=COLOR_CARD,
            fg=COLOR_MUTED,
            font=(FONT, 9),
        ).pack(side="right", padx=(0, 8))
        self.estado_refreshing = False

        self.estado_cards: dict[str, tuple[tk.Label, tk.Frame]] = {}
        cards_frame = tk.Frame(parent, bg=COLOR_BG)
        cards_frame.pack(fill="x", padx=18, pady=(0, 10))
        resumen = (
            ("EXITOSO", COLOR_OK),
            ("ABIERTO", COLOR_INFO),
            ("PENDIENTE", COLOR_WARN),
            ("ERROR", COLOR_ERROR),
            ("FALTANTE", COLOR_ERROR),
            ("CONFIGURAR", COLOR_MUTED),
        )
        for index, (estado, color) in enumerate(resumen):
            cards_frame.columnconfigure(index, weight=1, uniform="cards")
            card = tk.Frame(cards_frame, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            count = tk.Label(card, text="0", bg=COLOR_CARD, fg=color, font=(FONT, 20, "bold"))
            count.pack(pady=(10, 0))
            tk.Label(card, text=estado.title(), bg=COLOR_CARD, fg=COLOR_MUTED, font=(FONT, 9)).pack(pady=(0, 10))
            self.estado_cards[estado] = (count, card)

        columns = ("tipo", "codigo", "nombre", "corte", "ventana", "limite", "occ", "estado", "revisado")
        headings = {
            "tipo": "Tipo",
            "codigo": "Código",
            "nombre": "Reporte",
            "corte": "Fecha corte",
            "ventana": "Inicio envío",
            "limite": "Hora límite",
            "occ": "Ocurr.",
            "estado": "Estado",
            "revisado": "Última revisión",
        }
        widths = {
            "tipo": 80, "codigo": 110, "nombre": 330, "corte": 95, "ventana": 95,
            "limite": 135, "occ": 60, "estado": 110, "revisado": 140,
        }
        self.estado_tree = self._table(parent, columns, headings, widths, height=13)
        self.estado_tree.bind("<Double-1>", lambda _event: self._show_section("cola"))

    def _refresh_estado(self, revisar_asfi: bool = False) -> None:
        if self.estado_refreshing:
            return
        self.estado_refreshing = True
        self.estado_refresh_button.configure(state="disabled")
        self.estado_loading.set(
            "Consultando ASFI..." if revisar_asfi else "Cargando estado..."
        )
        self.estado_progress.start(10)
        threading.Thread(
            target=self._refresh_estado_worker,
            args=(revisar_asfi,),
            daemon=True,
        ).start()

    def _refresh_estado_worker(self, revisar_asfi: bool) -> None:
        rows = []
        current = reportes_db.local_now()
        error = None
        try:
            if revisar_asfi:
                # Ejecutar la misma revisión que usa el monitor sin bloquear la GUI.
                from asfi_monitor import ejecutar_revision

                ejecutar_revision()

            current = reportes_db.local_now()
            conn = reportes_db.connect(self.db_path)
            try:
                rows = reportes_db.list_today_obligations(conn, current)
            finally:
                conn.close()
        except Exception as exc:
            error = exc

        try:
            self.after(0, self._finish_refresh_estado, rows, current, error, revisar_asfi)
        except tk.TclError:
            pass

    def _finish_refresh_estado(
        self, rows: list[dict], current: datetime, error: Optional[Exception], revisar_asfi: bool
    ) -> None:
        self.estado_refreshing = False
        self.estado_progress.stop()
        self.estado_refresh_button.configure(state="normal")
        if error is not None:
            self.estado_loading.set("Error al actualizar")
            messagebox.showerror(
                "Estado del día",
                f"No se pudo actualizar el estado:\n{error}",
                parent=self,
            )
            return

        self.estado_loading.set(
            "Estado actualizado" if revisar_asfi else "Estado cargado"
        )
        try:
            self._render_estado(rows, current)
        except Exception as exc:
            self.estado_loading.set("Error al mostrar el estado")
            messagebox.showerror("Estado del día", f"No se pudo mostrar el estado:\n{exc}", parent=self)

    def _render_estado(self, rows: list[dict], current: datetime) -> None:
        counts = {estado: 0 for estado in self.estado_cards}
        for row in self.estado_tree.get_children():
            self.estado_tree.delete(row)
        for row in rows:
            estado = row["estado"] or "ABIERTO"
            if estado in ("ABIERTO", "PENDIENTE") and row["fecha_hora_limite"]:
                try:
                    limite = datetime.fromisoformat(row["fecha_hora_limite"])
                    if current > limite:
                        estado = "FALTANTE"
                except ValueError:
                    pass
            counts[estado] = counts.get(estado, 0) + 1
            self.estado_tree.insert(
                "",
                "end",
                values=(
                    row["tipo_periodo"].title(),
                    row["codigo"],
                    row["nombre"],
                    _display_date(row["fecha_corte"]),
                    _display_date(row["fecha_inicio_envio"]),
                    _display_datetime(row["fecha_hora_limite"]),
                    row["ocurrencia"],
                    estado,
                    _display_datetime(row["ultima_revision"]),
                ),
                tags=(estado.lower(),),
            )
        for estado, (label, _card) in self.estado_cards.items():
            label.configure(text=str(counts.get(estado, 0)))

    # ---------- página: en cola (atrasados) ----------

    def _build_page_cola(self, parent: tk.Frame) -> None:
        header = self._page_header(
            parent,
            "Reportes vencidos",
            "No se enviaron y ya superaron su fecha y hora máxima de entrega",
        )
        actions = tk.Frame(header, bg=COLOR_CARD)
        actions.pack(side="right")
        ttk.Button(actions, text="Actualizar", style="Dash.TButton", command=self._refresh_cola).pack(side="left", padx=3)
        ttk.Button(
            actions, text="Quitar seleccionado", style="Dash.TButton", command=self._dismiss_selected_overdue
        ).pack(side="left", padx=3)
        ttk.Button(
            actions, text="Vaciar lista", style="Dash.TButton", command=self._dismiss_all_overdue
        ).pack(side="left", padx=3)

        self.cola_resumen = tk.StringVar(value="")
        tk.Label(
            parent,
            textvariable=self.cola_resumen,
            bg=COLOR_BG,
            fg=COLOR_MUTED,
            font=(FONT, 9),
            anchor="w",
        ).pack(fill="x", padx=18, pady=(0, 6))

        columns = ("corte", "codigo", "nombre", "tipo", "occ", "fecha_limite", "hora_limite", "estado", "atraso")
        headings = {
            "corte": "Fecha corte",
            "codigo": "Código",
            "nombre": "Reporte",
            "tipo": "Tipo",
            "occ": "Ocurr.",
            "fecha_limite": "Debía enviarse",
            "hora_limite": "Hora límite",
            "estado": "Estado",
            "atraso": "Días de atraso",
        }
        widths = {
            "corte": 95, "codigo": 110, "nombre": 330, "tipo": 80, "occ": 60,
            "fecha_limite": 110, "hora_limite": 90, "estado": 105, "atraso": 95,
        }
        self.cola_tree = self._table(parent, columns, headings, widths, height=15)

    def _refresh_cola(self) -> None:
        try:
            reportes_db.ensure_obligations(self.conn)
            rows = reportes_db.list_overdue_obligations(self.conn)
        except Exception as exc:
            messagebox.showerror("Vencidos", f"No se pudo consultar la base:\n{exc}", parent=self)
            return
        today = reportes_db.local_now().date()
        for item in self.cola_tree.get_children():
            self.cola_tree.delete(item)
        for row in rows:
            limite = reportes_db.parse_date(row["fecha_limite"])
            atraso = (today - limite).days if limite else ""
            estado = row["estado"] or "ABIERTO"
            self.cola_tree.insert(
                "",
                "end",
                iid=str(row["id"]),
                values=(
                    row["fecha_corte"],
                    row["codigo"] or "-",
                    row["nombre"],
                    row["tipo_periodo"].title(),
                    row["ocurrencia"],
                    row["fecha_limite"],
                    row["hora_limite"],
                    estado,
                    atraso,
                ),
                tags=(estado.lower(),),
            )
        self.cola_resumen.set(
            f"{len(rows)} envíos vencidos sin registro de envío | "
            "Los estados se refrescan con cada ejecución del monitor"
        )

    def _dismiss_selected_overdue(self) -> None:
        selection = self.cola_tree.selection()
        if not selection:
            messagebox.showwarning("Vencidos", "Seleccione un reporte primero.", parent=self)
            return
        item = self.cola_tree.item(selection[0])
        nombre = item["values"][2]
        if not messagebox.askyesno(
            "Confirmar",
            f"¿Quitar '{nombre}' de la lista de vencidos?\n\n"
            "Dejará de generar alertas para este envío. Esta acción no se puede deshacer.",
            parent=self,
        ):
            return
        try:
            reportes_db.dismiss_obligation(self.conn, int(selection[0]))
        except Exception as exc:
            messagebox.showerror("Vencidos", f"No se pudo quitar el reporte:\n{exc}", parent=self)
            return
        self._refresh_cola()

    def _dismiss_all_overdue(self) -> None:
        pendientes = len(self.cola_tree.get_children())
        if not pendientes:
            messagebox.showinfo("Vencidos", "No hay reportes vencidos para quitar.", parent=self)
            return
        if not messagebox.askyesno(
            "Confirmar",
            f"¿Quitar los {pendientes} reportes vencidos de la lista?\n\n"
            "Dejarán de generar alertas. Esta acción no se puede deshacer.",
            parent=self,
        ):
            return
        try:
            reportes_db.dismiss_overdue_obligations(self.conn)
        except Exception as exc:
            messagebox.showerror("Vencidos", f"No se pudo vaciar la lista:\n{exc}", parent=self)
            return
        self._refresh_cola()

    # ---------- página: catálogo de reportes ----------

    def _build_page_reportes(self, parent: tk.Frame) -> None:
        header = self._page_header(
            parent,
            "Catálogo de reportes",
            "Reportes, reglas de calendario y validaciones",
        )
        actions = tk.Frame(header, bg=COLOR_CARD)
        actions.pack(side="right")
        ttk.Button(actions, text="Historial", style="Dash.TButton", command=self._show_history).pack(side="left", padx=3)
        ttk.Button(actions, text="Nuevo", style="Dash.TButton", command=self._new_report).pack(side="left", padx=3)
        ttk.Button(actions, text="Editar", style="Dash.TButton", command=self._edit_report).pack(side="left", padx=3)
        ttk.Button(actions, text="Activar/Desactivar", style="Dash.TButton", command=self._toggle_active).pack(side="left", padx=3)
        ttk.Button(actions, text="Validación ON/OFF", style="Dash.TButton", command=self._toggle_validation).pack(side="left", padx=3)

        search_bar = tk.Frame(parent, bg=COLOR_BG)
        search_bar.pack(fill="x", padx=18, pady=(0, 6))
        tk.Label(search_bar, text="Buscar:", bg=COLOR_BG, fg=COLOR_MUTED, font=(FONT, 9)).pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_args: self._refresh_reportes())
        ttk.Entry(search_bar, textvariable=self.search_var, width=40).pack(side="left", padx=(6, 0))

        columns = ("codigo", "nombre", "tipo", "activo", "validacion", "reglas")
        headings = {
            "codigo": "Código",
            "nombre": "Nombre",
            "tipo": "Tipo",
            "activo": "Activo",
            "validacion": "Validación",
            "reglas": "Reglas",
        }
        widths = {"codigo": 130, "nombre": 520, "tipo": 100, "activo": 80, "validacion": 95, "reglas": 70}
        self.reportes_tree = self._table(parent, columns, headings, widths, height=15)
        self.reportes_tree.bind("<Double-1>", lambda _event: self._edit_report())
        self.reportes_status = tk.StringVar(value="")
        tk.Label(
            parent,
            textvariable=self.reportes_status,
            bg=COLOR_BG,
            fg=COLOR_MUTED,
            font=(FONT, 9),
            anchor="w",
        ).pack(fill="x", padx=18, pady=(0, 12))

    def _refresh_reportes(self) -> None:
        try:
            catalog = reportes_db.get_catalog(self.conn)
        except Exception as exc:
            messagebox.showerror("Catálogo", f"No se pudo consultar la base:\n{exc}", parent=self)
            return
        self.items = {str(item["id"]): item for item in catalog}
        filtro = self.search_var.get().strip().lower() if hasattr(self, "search_var") else ""
        for row in self.reportes_tree.get_children():
            self.reportes_tree.delete(row)
        visibles = 0
        for item_id, item in self.items.items():
            texto = f"{item['codigo']} {item['nombre']} {item['tipo_periodo']}".lower()
            if filtro and filtro not in texto:
                continue
            visibles += 1
            self.reportes_tree.insert(
                "",
                "end",
                iid=item_id,
                values=(
                    item["codigo"],
                    item["nombre"],
                    item["tipo_periodo"],
                    "Sí" if item["activo"] else "No",
                    "Sí" if item["validacion_activa"] else "No",
                    len(item["reglas"]),
                ),
            )
        self.reportes_status.set(
            f"{visibles} de {len(self.items)} reportes | Base: {self.db_path.name}"
        )

    def _selected_report(self) -> Optional[dict]:
        selection = self.reportes_tree.selection()
        if not selection:
            messagebox.showwarning("Reporte", "Seleccione un reporte primero.", parent=self)
            return None
        return self.items.get(selection[0])

    def _new_report(self) -> None:
        dialog = ReportDialog(self)
        self.wait_window(dialog)
        if dialog.result:
            self._save_report(dialog.result)

    def _edit_report(self) -> None:
        report = self._selected_report()
        if not report:
            return
        dialog = ReportDialog(self, report)
        self.wait_window(dialog)
        if dialog.result:
            self._save_report(dialog.result)

    def _save_report(self, report: dict) -> None:
        try:
            reportes_db.save_report(
                self.conn,
                report["id"],
                report["codigo"],
                report["nombre"],
                report["tipo_periodo"],
                report["activo"],
                report["validacion_activa"],
                report["descripcion"],
                report["reglas"],
                report["aliases"],
            )
            self._refresh_reportes()
        except sqlite3.IntegrityError as exc:
            messagebox.showerror("Reporte", f"Código o alias duplicado:\n{exc}", parent=self)
        except Exception as exc:
            messagebox.showerror("Reporte", f"No se pudo guardar:\n{exc}", parent=self)

    def _toggle_active(self) -> None:
        report = self._selected_report()
        if not report:
            return
        reportes_db.set_report_flags(
            self.conn, report["id"], not bool(report["activo"]), bool(report["validacion_activa"])
        )
        self._refresh_reportes()

    def _toggle_validation(self) -> None:
        report = self._selected_report()
        if not report:
            return
        reportes_db.set_report_flags(
            self.conn, report["id"], bool(report["activo"]), not bool(report["validacion_activa"])
        )
        self._refresh_reportes()

    def _show_history(self) -> None:
        window = tk.Toplevel(self)
        window.title("Historial de incumplimientos")
        window.geometry("900x450")
        window.configure(bg=COLOR_BG)
        window.transient(self)
        frame = ttk.Frame(window, padding=10)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        columns = ("fecha", "codigo", "reporte", "tipo", "ocurrencia", "estado", "detectado", "resuelto")
        tree = ttk.Treeview(frame, columns=columns, show="headings", style="Dash.Treeview")
        headings = {
            "fecha": "Fecha corte",
            "codigo": "Código",
            "reporte": "Reporte",
            "tipo": "Tipo",
            "ocurrencia": "Ocurrencia",
            "estado": "Estado",
            "detectado": "Detectado",
            "resuelto": "Resuelto",
        }
        widths = {"fecha": 90, "codigo": 120, "reporte": 320, "tipo": 80, "ocurrencia": 80, "estado": 90, "detectado": 150, "resuelto": 150}
        for column in columns:
            tree.heading(column, text=headings[column])
            tree.column(column, width=widths[column], anchor="center" if column != "reporte" else "w")
        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        for item in reportes_db.list_failures(self.conn):
            tree.insert(
                "",
                "end",
                values=(
                    item["fecha_corte"],
                    item.get("codigo") or "-",
                    item["nombre_snapshot"],
                    item["tipo_periodo"],
                    item["ocurrencia"],
                    item["estado"],
                    item["detectado_en"],
                    item.get("resuelto_en") or "Pendiente",
                ),
            )
        ttk.Button(window, text="Cerrar", command=window.destroy).pack(pady=(0, 10))

    # ---------- página: credenciales ----------

    def _build_page_credenciales(self, parent: tk.Frame) -> None:
        self._page_header(
            parent,
            "Credenciales ASFI/SCIP",
            "Las usa el monitor para iniciar sesión en cada ciclo",
        )

        card = tk.Frame(parent, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
        card.pack(fill="x", padx=18, pady=(0, 10))
        inner = tk.Frame(card, bg=COLOR_CARD)
        inner.pack(fill="x", padx=20, pady=16)
        inner.columnconfigure(1, weight=1)

        self.username = tk.StringVar()
        self.password = tk.StringVar()
        ttk.Label(inner, text="Usuario", style="Dash.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(inner, textvariable=self.username, width=40).grid(row=0, column=1, sticky="ew", pady=4)
        ttk.Label(inner, text="Contraseña", style="Dash.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=4)
        ttk.Entry(inner, textvariable=self.password, show="*", width=40).grid(row=1, column=1, sticky="ew", pady=4)
        ttk.Button(inner, text="Guardar credenciales", style="Dash.TButton", command=self._save_credentials).grid(
            row=0, column=2, rowspan=2, padx=(12, 0)
        )
        ttk.Label(
            inner,
            text="La contraseña se guarda cifrada con DPAPI de Windows y solo este usuario puede leerla.",
            style="Dash.TLabel",
            foreground=COLOR_MUTED,
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(10, 0))

        self.credentials_status = tk.StringVar(value="")
        tk.Label(
            parent,
            textvariable=self.credentials_status,
            bg=COLOR_BG,
            fg=COLOR_MUTED,
            font=(FONT, 9),
            anchor="w",
        ).pack(fill="x", padx=18)

    def _load_credentials(self) -> None:
        try:
            usuario, password = reportes_db.get_credentials(self.conn)
            self.username.set(usuario)
            self.password.set(password)
            actualizado = reportes_db.credentials_updated_at(self.conn)
            if usuario:
                self.credentials_status.set(
                    f"Credenciales guardadas para '{usuario}' | Última actualización: {actualizado or '-'}"
                )
            else:
                self.credentials_status.set(
                    "Sin credenciales guardadas. El monitor no podrá iniciar sesión hasta que las registre."
                )
        except Exception as exc:
            messagebox.showwarning(
                "Credenciales",
                f"No se pudieron leer las credenciales guardadas:\n{exc}",
                parent=self,
            )

    def _save_credentials(self) -> None:
        try:
            reportes_db.save_credentials(self.conn, self.username.get(), self.password.get())
            self._load_credentials()
            messagebox.showinfo("Credenciales", "Credenciales guardadas correctamente.", parent=self)
        except ValueError as exc:
            messagebox.showerror("Credenciales", str(exc), parent=self)
        except Exception as exc:
            messagebox.showerror("Credenciales", f"No se pudieron guardar:\n{exc}", parent=self)

    def _close(self) -> None:
        self.conn.close()
        self.destroy()


def ejecutar_gui(db_path: Optional[Path] = None) -> None:
    db_path = db_path or reportes_db.resolve_path("asfi_monitor.db")
    reportes_db.initialize_database(
        db_path,
        reportes_db.resolve_path("reportes_seed.json"),
        reportes_db.resolve_path("reportes_no_enviados.json"),
    )
    app = Dashboard(db_path)
    app.mainloop()


if __name__ == "__main__":
    ejecutar_gui()
