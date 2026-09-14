"""Panel de administración de ASFI Monitor: dashboard con menú lateral.

Secciones:
  - Estado del día: obligaciones del período vigente por reporte.
  - En cola: obligaciones de fechas anteriores que aún no se envían.
  - Catálogo de reportes: altas, bajas y reglas de calendario.
  - Credenciales: acceso a ASFI/SCIP protegido con DPAPI.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import threading
from typing import Optional

from .formatters import (
    _display_date,
    _display_datetime,
    _optional_int,
    _overdue_days,
)
from asfi_monitor_app.storage import api as reportes_db


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
COLOR_ERROR = "#dc2626"
COLOR_INFO = "#0284c7"
COLOR_PENDING = "#ca8a04"
COLOR_LATE = "#ea580c"
COLOR_MISSING = "#7c3aed"
COLOR_OVERDUE = "#0891b2"
FONT = "Segoe UI"

ESTADO_COLORS = {
    "EXITOSO": COLOR_OK,
    "ABIERTO": COLOR_INFO,
    "PENDIENTE": COLOR_PENDING,
    "EXITOSO_TARDIO": COLOR_LATE,
    "ERROR": COLOR_ERROR,
    "FALTANTE": COLOR_MISSING,
    "VENCIDO_SIN_ENVIAR": COLOR_OVERDUE,
    "CONFIGURAR": COLOR_MUTED,
}

# Orden en que se muestran los grupos del catálogo por tipo de período.
TIPO_PERIODO_ORDEN = ("diario", "semanal", "mensual", "trimestral", "semestral", "anual", "otro")
TIPO_PERIODO_TITULO = {
    "diario": "DIARIOS",
    "semanal": "SEMANALES",
    "mensual": "MENSUALES",
    "trimestral": "TRIMESTRALES",
    "semestral": "SEMESTRALES",
    "anual": "ANUALES",
    "otro": "OTROS",
}
from .dialogs import ReportDialog, RuleDialog


class Dashboard(tk.Tk):
    SECTIONS = (
        ("estado", "Estado del día"),
        ("cola", "Reportes Vencidos"),
        ("reportes", "Catálogo de reportes"),
        ("credenciales", "Credenciales"),
        ("configuracion", "Configuración"),
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
            text="Versión 1.0.0",
            bg=COLOR_SIDEBAR,
            fg=COLOR_MUTED,
            font=(FONT, 8),
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            footer,
            text="ASFI/SCIP Monitor - Comarapa RL",
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
            "configuracion": self._build_page_configuracion,
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

    def _table(
        self,
        parent: tk.Frame,
        columns: tuple,
        headings: dict,
        widths: dict,
        height: int = 16,
        show: str = "headings",
    ) -> ttk.Treeview:
        wrapper = tk.Frame(parent, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
        wrapper.pack(fill="both", expand=True, padx=18, pady=(0, 14))
        tree = ttk.Treeview(
            wrapper, columns=columns, show=show, selectmode="browse", style="Dash.Treeview", height=height
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
            "configuracion": self._load_app_configuration,
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
            ("EXITOSO", COLOR_OK, "Exitoso"),
            ("ABIERTO", COLOR_INFO, "Abierto"),
            ("EXITOSO_TARDIO", COLOR_LATE, "Exitoso tardío"),
            ("ERROR", COLOR_ERROR, "Error"),
            ("FALTANTE", COLOR_MISSING, "Sin envío"),
            ("VENCIDO_SIN_ENVIAR", COLOR_OVERDUE, "Vencido sin enviar"),
        )
        for index, (estado, color, titulo) in enumerate(resumen):
            cards_frame.columnconfigure(index, weight=1, uniform="cards")
            card = tk.Frame(cards_frame, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
            card.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
            count = tk.Label(card, text="0", bg=COLOR_CARD, fg=color, font=(FONT, 20, "bold"))
            count.pack(pady=(10, 0))
            tk.Label(card, text=titulo, bg=COLOR_CARD, fg=COLOR_MUTED, font=(FONT, 9)).pack(pady=(0, 10))
            self.estado_cards[estado] = (count, card)

        columns = ("tipo", "nombre", "corte", "envio", "limite", "estado", "revisado")
        headings = {
            "tipo": "Tipo",
            "nombre": "Reporte",
            "corte": "Fecha corte",
            "envio": "Fecha envío",
            "limite": "Hora límite",
            "estado": "Estado",
            "revisado": "Última revisión",
        }
        widths = {
            "tipo": 80, "nombre": 440, "corte": 95, "envio": 135,
            "limite": 135, "estado": 110, "revisado": 140,
        }
        self.estado_tree = self._table(parent, columns, headings, widths, height=13)
        self.estado_tree.tag_configure(
            "grupo_estado",
            background=COLOR_BG,
            foreground=COLOR_TEXT,
            font=(FONT, 9, "bold"),
        )
        self.estado_tree.bind("<Double-1>", self._on_estado_double_click)

    def _on_estado_double_click(self, event) -> None:
        item_id = self.estado_tree.identify_row(event.y)
        if item_id and "grupo_estado" in self.estado_tree.item(item_id, "tags"):
            return
        self._show_section("cola")

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
                from asfi_monitor_app.application.monitor_service import ejecutar_revision

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
        grouped_rows: dict[str, list[tuple[dict, str]]] = {}
        for row in self.estado_tree.get_children():
            self.estado_tree.delete(row)
        for row in rows:
            estado = row["estado"] or "ABIERTO"
            if estado in ("ABIERTO", "PENDIENTE") and row["fecha_hora_limite"]:
                try:
                    limite = datetime.fromisoformat(row["fecha_hora_limite"])
                    if current > limite:
                        estado = (
                            "VENCIDO_SIN_ENVIAR"
                            if row["tipo_periodo"] == "semanal"
                            else "FALTANTE"
                        )
                except ValueError:
                    pass
            elif estado == "ERROR" and row["tipo_periodo"] == "semanal" and row["fecha_hora_limite"]:
                try:
                    if current > datetime.fromisoformat(row["fecha_hora_limite"]):
                        estado = "VENCIDO_SIN_ENVIAR"
                except ValueError:
                    pass
            counts[estado] = counts.get(estado, 0) + 1

            tipo = (row.get("tipo_periodo") or "otro").lower()
            grouped_rows.setdefault(tipo, []).append((row, estado))

        ordered_types = list(TIPO_PERIODO_ORDEN)
        ordered_types.extend(tipo for tipo in grouped_rows if tipo not in ordered_types)
        for tipo in ordered_types:
            type_rows = grouped_rows.get(tipo)
            if not type_rows:
                continue
            titulo = TIPO_PERIODO_TITULO.get(tipo, tipo.upper())
            self.estado_tree.insert(
                "",
                "end",
                values=(titulo, f"{len(type_rows)} reportes", "", "", "", "", ""),
                tags=("grupo_estado",),
            )
            for row, estado in type_rows:
                self.estado_tree.insert(
                    "",
                    "end",
                    values=(
                        row["tipo_periodo"].title(),
                        row["nombre"],
                        _display_date(row["fecha_corte"]),
                        _display_datetime(row["fecha_envio"]),
                        _display_datetime(row["fecha_hora_limite"]),
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
            "Reportes Vencidos",
            "Vencidos sin envío y envíos exitosos realizados fuera de plazo",
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

        columns = ("corte", "nombre", "tipo", "fecha_limite", "estado", "atraso")
        headings = {
            "corte": "Fecha corte",
            "nombre": "Reporte",
            "tipo": "Tipo",
            "fecha_limite": "Debía enviarse",
            "estado": "Estado",
            "atraso": "Días de atraso",
        }
        widths = {
            "corte": 95, "nombre": 430, "tipo": 85,
            "fecha_limite": 155, "estado": 125, "atraso": 105,
        }
        self.cola_tree = self._table(parent, columns, headings, widths, height=15)
        self.cola_tree.tag_configure(
            "grupo_cola",
            background=COLOR_BG,
            foreground=COLOR_TEXT,
            font=(FONT, 9, "bold"),
        )

    def _refresh_cola(self) -> None:
        try:
            reportes_db.ensure_obligations(self.conn)
            rows = reportes_db.list_overdue_obligations(self.conn)
        except Exception as exc:
            messagebox.showerror("Reportes vencidos", f"No se pudo consultar la base:\n{exc}", parent=self)
            return
        current = reportes_db.local_now()
        for item in self.cola_tree.get_children():
            self.cola_tree.delete(item)
        groups = (
            ("sin_envio", "VENCIDOS SIN ENVÍO", [row for row in rows if row["estado"] != "EXITOSO_TARDIO"]),
            ("tardios", "ENVIADOS EXITOSOS TARDE", [row for row in rows if row["estado"] == "EXITOSO_TARDIO"]),
        )
        for group_key, title, group_rows in groups:
            if not group_rows:
                continue
            self.cola_tree.insert(
                "",
                "end",
                iid=f"grupo_cola_{group_key}",
                values=("", f"{title} ({len(group_rows)})", "", "", "", ""),
                tags=("grupo_cola",),
            )
            for row in group_rows:
                estado = row["estado"] or "ABIERTO"
                self.cola_tree.insert(
                    "",
                    "end",
                    iid=str(row["id"]),
                    values=(
                        row["fecha_corte"],
                        row["nombre"],
                        row["tipo_periodo"].title(),
                        _display_datetime(row["fecha_hora_limite"]),
                        estado,
                        _overdue_days(row, current),
                    ),
                    tags=(estado.lower(),),
                )
        sin_envio = sum(1 for row in rows if row["estado"] != "EXITOSO_TARDIO")
        tardios = len(rows) - sin_envio
        self.cola_resumen.set(
            f"{sin_envio} vencidos sin envío | {tardios} enviados exitosos tarde | "
            "Los estados se refrescan con cada ejecución del monitor"
        )

    def _dismiss_selected_overdue(self) -> None:
        selection = self.cola_tree.selection()
        if not selection:
            messagebox.showwarning("Reportes vencidos", "Seleccione un reporte primero.", parent=self)
            return
        if "grupo_cola" in self.cola_tree.item(selection[0], "tags"):
            messagebox.showwarning("Reportes vencidos", "Seleccione un reporte, no un encabezado.", parent=self)
            return
        item = self.cola_tree.item(selection[0])
        nombre = item["values"][1]
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
            messagebox.showerror("Reportes vencidos", f"No se pudo quitar el reporte:\n{exc}", parent=self)
            return
        self._refresh_cola()

    def _dismiss_all_overdue(self) -> None:
        pendientes = sum(
            1
            for item_id in self.cola_tree.get_children()
            if "grupo_cola" not in self.cola_tree.item(item_id, "tags")
        )
        if not pendientes:
            messagebox.showinfo("Reportes vencidos", "No hay reportes vencidos para quitar.", parent=self)
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
            messagebox.showerror("Reportes vencidos", f"No se pudo vaciar la lista:\n{exc}", parent=self)
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
        self.reportes_tree = self._table(parent, columns, headings, widths, height=15, show="tree headings")
        self.reportes_tree.column("#0", width=26, stretch=False, anchor="center")
        self.reportes_tree.tag_configure(
            "grupo", background=COLOR_BG, foreground=COLOR_TEXT, font=(FONT, 9, "bold")
        )
        self.reportes_tree.bind("<Double-1>", self._on_reportes_double_click)
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

        visibles: dict[str, list[dict]] = {}
        for item_id, item in self.items.items():
            texto = f"{item['codigo']} {item['nombre']} {item['tipo_periodo']}".lower()
            if filtro and filtro not in texto:
                continue
            tipo = (item.get("tipo_periodo") or "otro").lower()
            if tipo not in TIPO_PERIODO_TITULO:
                tipo = "otro"
            visibles.setdefault(tipo, []).append({**item, "_id": item_id})

        total_visibles = 0
        for tipo in TIPO_PERIODO_ORDEN:
            reportes = visibles.get(tipo)
            if not reportes:
                continue
            reportes.sort(key=lambda item: (item["codigo"] or "", item["nombre"] or ""))
            total_visibles += len(reportes)
            grupo_iid = f"grupo_{tipo}"
            titulo = TIPO_PERIODO_TITULO[tipo]
            self.reportes_tree.insert(
                "",
                "end",
                iid=grupo_iid,
                text="",
                values=("", f"{titulo} ({len(reportes)})", "", "", "", ""),
                tags=("grupo",),
                open=True,
            )
            for item in reportes:
                self.reportes_tree.insert(
                    grupo_iid,
                    "end",
                    iid=item["_id"],
                    text="",
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
            f"{total_visibles} de {len(self.items)} reportes | Base: {self.db_path.name}"
        )

    def _on_reportes_double_click(self, event) -> None:
        item_id = self.reportes_tree.identify_row(event.y)
        if item_id.startswith("grupo_"):
            self.reportes_tree.item(item_id, open=not self.reportes_tree.item(item_id, "open"))
            return
        self._edit_report()

    def _selected_report(self) -> Optional[dict]:
        selection = self.reportes_tree.selection()
        if not selection or selection[0].startswith("grupo_"):
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

    # ---------- página: configuración ----------

    def _build_page_configuracion(self, parent: tk.Frame) -> None:
        self._page_header(
            parent,
            "Configuración del monitor",
            "Conexión, frecuencia de inspección y fechas de consulta",
        )

        card = tk.Frame(parent, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
        card.pack(fill="x", padx=18, pady=(0, 10))
        inner = tk.Frame(card, bg=COLOR_CARD)
        inner.pack(fill="x", padx=20, pady=16)
        inner.columnconfigure(1, weight=1)

        self.config_url = tk.StringVar()
        self.config_interval = tk.StringVar()
        ttk.Label(inner, text="Enlace base de ASFI/SCIP", style="Dash.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 8), pady=5
        )
        ttk.Entry(inner, textvariable=self.config_url, width=65).grid(
            row=0, column=1, sticky="ew", pady=5
        )
        ttk.Label(inner, text="Ejemplo: https://appweb.asfi.gob.bo/SCIP", style="Dash.TLabel").grid(
            row=1, column=1, sticky="w", pady=(0, 8)
        )
        ttk.Label(inner, text="Intervalo de inspección (minutos)", style="Dash.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=5
        )
        ttk.Entry(inner, textvariable=self.config_interval, width=12).grid(
            row=2, column=1, sticky="w", pady=5
        )
        ttk.Label(inner, text="Por defecto: 15 minutos", style="Dash.TLabel").grid(
            row=3, column=1, sticky="w", pady=(0, 8)
        )

        dates_card = tk.Frame(parent, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
        dates_card.pack(fill="x", padx=18, pady=(0, 10))
        dates = tk.Frame(dates_card, bg=COLOR_CARD)
        dates.pack(fill="x", padx=20, pady=16)
        dates.columnconfigure(1, weight=1)

        self.config_auto_dates = tk.BooleanVar(value=True)
        self.config_start_date = tk.StringVar()
        self.config_end_date = tk.StringVar()
        ttk.Label(
            dates, text="Rango de fechas de corte", font=(FONT, 10, "bold"), style="Dash.TLabel"
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Checkbutton(
            dates,
            text="Usar automáticamente el día de ayer",
            variable=self.config_auto_dates,
            command=self._toggle_date_mode,
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, 8))
        ttk.Label(dates, text="Fecha corte inicial (dd/mm/aaaa)", style="Dash.TLabel").grid(
            row=2, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.config_start_entry = ttk.Entry(dates, textvariable=self.config_start_date, width=18)
        self.config_start_entry.grid(row=2, column=1, sticky="w", pady=4)
        ttk.Label(dates, text="Fecha corte final (dd/mm/aaaa)", style="Dash.TLabel").grid(
            row=3, column=0, sticky="w", padx=(0, 8), pady=4
        )
        self.config_end_entry = ttk.Entry(dates, textvariable=self.config_end_date, width=18)
        self.config_end_entry.grid(row=3, column=1, sticky="w", pady=4)
        ttk.Label(
            dates,
            text="El modo automático se actualiza solo cada día. Desmárcalo para consultar un rango histórico.",
            style="Dash.TLabel",
        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))

        holidays_card = tk.Frame(parent, bg=COLOR_CARD, highlightthickness=1, highlightbackground=COLOR_BORDER)
        holidays_card.pack(fill="both", expand=True, padx=18, pady=(0, 10))
        holidays_header = tk.Frame(holidays_card, bg=COLOR_CARD)
        holidays_header.pack(fill="x", padx=20, pady=(12, 6))
        tk.Label(
            holidays_header,
            text="Feriados no hábiles",
            bg=COLOR_CARD,
            fg=COLOR_TEXT,
            font=(FONT, 10, "bold"),
        ).pack(side="left")
        tk.Label(
            holidays_header,
            text="Los plazos hábiles consideran lunes a viernes y estas fechas",
            bg=COLOR_CARD,
            fg=COLOR_MUTED,
            font=(FONT, 9),
        ).pack(side="left", padx=(12, 0))
        holidays_wrapper = tk.Frame(holidays_card, bg=COLOR_CARD)
        holidays_wrapper.pack(fill="both", expand=True, padx=20, pady=(0, 8))
        self.holidays_tree = ttk.Treeview(
            holidays_wrapper,
            columns=("fecha", "descripcion"),
            show="headings",
            selectmode="browse",
            height=5,
        )
        self.holidays_tree.heading("fecha", text="Fecha")
        self.holidays_tree.heading("descripcion", text="Descripción")
        self.holidays_tree.column("fecha", width=110, anchor="center")
        self.holidays_tree.column("descripcion", width=430, anchor="w")
        holidays_scroll = ttk.Scrollbar(
            holidays_wrapper, orient="vertical", command=self.holidays_tree.yview
        )
        self.holidays_tree.configure(yscrollcommand=holidays_scroll.set)
        self.holidays_tree.grid(row=0, column=0, sticky="nsew")
        holidays_scroll.grid(row=0, column=1, sticky="ns")
        holidays_wrapper.rowconfigure(0, weight=1)
        holidays_wrapper.columnconfigure(0, weight=1)
        holidays_actions = tk.Frame(holidays_card, bg=COLOR_CARD)
        holidays_actions.pack(fill="x", padx=20, pady=(0, 10))
        ttk.Button(holidays_actions, text="Agregar feriado", command=self._add_holiday).pack(side="left")
        ttk.Button(
            holidays_actions,
            text="Eliminar seleccionado",
            command=self._delete_holiday,
        ).pack(side="left", padx=(8, 0))

        actions = tk.Frame(parent, bg=COLOR_BG)
        actions.pack(fill="x", padx=18, pady=(0, 6))
        ttk.Button(
            actions,
            text="Guardar configuración",
            style="Dash.TButton",
            command=self._save_app_configuration,
        ).pack(side="left")
        self.config_status = tk.StringVar(value="")
        tk.Label(
            actions,
            textvariable=self.config_status,
            bg=COLOR_BG,
            fg=COLOR_MUTED,
            font=(FONT, 9),
            anchor="w",
        ).pack(side="left", padx=(12, 0))

    def _refresh_holidays(self) -> None:
        if not hasattr(self, "holidays_tree"):
            return
        for item in self.holidays_tree.get_children():
            self.holidays_tree.delete(item)
        for holiday in reportes_db.list_holidays(self.conn):
            self.holidays_tree.insert(
                "",
                "end",
                iid=holiday["fecha"],
                values=(_display_date(holiday["fecha"]), holiday["descripcion"]),
            )

    def _add_holiday(self) -> None:
        value = simpledialog.askstring(
            "Agregar feriado", "Fecha (dd/mm/aaaa):", parent=self
        )
        if value is None:
            return
        description = simpledialog.askstring(
            "Agregar feriado", "Descripción:", parent=self
        )
        if description is None:
            return
        try:
            reportes_db.set_holiday(self.conn, value, description)
            self._refresh_holidays()
        except ValueError as exc:
            messagebox.showerror("Feriado", str(exc), parent=self)

    def _delete_holiday(self) -> None:
        selected = self.holidays_tree.selection()
        if not selected:
            messagebox.showwarning("Feriado", "Seleccione un feriado primero.", parent=self)
            return
        holiday = selected[0]
        if not messagebox.askyesno(
            "Eliminar feriado", f"¿Eliminar el feriado {holiday}?", parent=self
        ):
            return
        reportes_db.delete_holiday(self.conn, holiday)
        self._refresh_holidays()

    def _toggle_date_mode(self) -> None:
        state = "disabled" if self.config_auto_dates.get() else "normal"
        self.config_start_entry.configure(state=state)
        self.config_end_entry.configure(state=state)
        if self.config_auto_dates.get():
            ayer = reportes_db.local_now().date() - timedelta(days=1)
            texto = reportes_db.format_asfi_date(ayer)
            self.config_start_date.set(texto)
            self.config_end_date.set(texto)

    def _load_app_configuration(self) -> None:
        try:
            url = reportes_db.get_app_setting(self.conn, "monitor_url_base")
            intervalo = reportes_db.get_app_setting(self.conn, "monitor_intervalo_minutos")
            inicio = reportes_db.get_app_setting(self.conn, "monitor_fecha_inicio_corte")
            fin = reportes_db.get_app_setting(self.conn, "monitor_fecha_fin_corte")
            ayer = reportes_db.local_now().date() - timedelta(days=1)
            self.config_url.set(url or "https://appweb.asfi.gob.bo/SCIP")
            self.config_interval.set(intervalo or "15")
            self.config_auto_dates.set(not (inicio and fin))
            self.config_start_date.set(
                _display_date(inicio) if inicio else reportes_db.format_asfi_date(ayer)
            )
            self.config_end_date.set(
                _display_date(fin) if fin else reportes_db.format_asfi_date(ayer)
            )
            self._toggle_date_mode()
            self._refresh_holidays()
            self.config_status.set("Configuración cargada")
        except Exception as exc:
            self.config_status.set("No se pudo cargar la configuración")
            messagebox.showwarning(
                "Configuración", f"No se pudo leer la configuración guardada:\n{exc}", parent=self
            )

    def _save_app_configuration(self) -> None:
        url = self.config_url.get().strip().rstrip("/")
        if not url or not url.lower().startswith(("http://", "https://")) or any(
            char.isspace() for char in url
        ):
            messagebox.showerror(
                "Configuración",
                "El enlace debe comenzar con http:// o https:// y no contener espacios.",
                parent=self,
            )
            return
        try:
            intervalo = _optional_int(self.config_interval.get(), "Intervalo de inspección", 1)
            if intervalo is None:
                raise ValueError("El intervalo de inspección es obligatorio")
            inicio = reportes_db.parse_date(self.config_start_date.get())
            fin = reportes_db.parse_date(self.config_end_date.get())
            if self.config_auto_dates.get():
                inicio_iso = None
                fin_iso = None
            else:
                if inicio is None or fin is None:
                    raise ValueError("Las fechas deben tener formato dd/mm/aaaa")
                if inicio > fin:
                    raise ValueError("La fecha inicial no puede ser posterior a la fecha final")
                inicio_iso = inicio.isoformat()
                fin_iso = fin.isoformat()

            reportes_db.set_app_settings(
                self.conn,
                {
                    "monitor_url_base": url,
                    "monitor_intervalo_minutos": str(intervalo),
                    "monitor_fecha_inicio_corte": inicio_iso,
                    "monitor_fecha_fin_corte": fin_iso,
                },
            )
            self.config_status.set("Configuración guardada correctamente")
            messagebox.showinfo(
                "Configuración", "La configuración fue guardada correctamente.", parent=self
            )
        except ValueError as exc:
            messagebox.showerror("Configuración", str(exc), parent=self)
        except Exception as exc:
            messagebox.showerror("Configuración", f"No se pudo guardar:\n{exc}", parent=self)

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
