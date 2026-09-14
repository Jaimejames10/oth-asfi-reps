"""Adaptadores de notificacion para Windows y Plyer."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional


def notificar(
    titulo: str,
    mensaje: str,
    urgente: bool = False,
    *,
    logger: Optional[logging.Logger] = None,
    icon_path: Optional[Path] = None,
) -> None:
    """Envía una alerta usando el primer proveedor disponible."""
    log = logger or logging.getLogger("asfi_monitor")

    titulo_log = titulo.encode("ascii", "replace").decode("ascii")
    mensaje_log = mensaje[:100].encode("ascii", "replace").decode("ascii")
    log.info(f"[NOTIFICACION] {titulo_log}: {mensaje_log}")

    titulo_limpio = titulo[:128]
    mensaje_limpio = mensaje[:512]
    titulo_ps = titulo_limpio.replace("'", "''")
    mensaje_ps = mensaje_limpio.replace("'", "''")

    def metodo_messagebox():
        try:
            import subprocess

            ps_script = f"""
            [void][System.Reflection.Assembly]::LoadWithPartialName('System.Windows.Forms')
            [System.Windows.Forms.MessageBox]::Show('{mensaje_ps}', '{titulo_ps}', [System.Windows.Forms.MessageBoxButtons]::OK, [System.Windows.Forms.MessageBoxIcon]::Warning)
            """
            subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Normal", "-Command", ps_script],
                timeout=120,
                capture_output=True,
                text=True,
            )
            log.debug("Notificacion enviada via PowerShell MessageBox")
            return True
        except Exception as exc:
            log.debug(f"PowerShell MessageBox: {type(exc).__name__}")
            return False

    def metodo_plyer():
        try:
            from plyer import notification

            duracion = 30 if urgente else 15
            notification.notify(
                title=titulo_limpio,
                message=mensaje_limpio[:256],
                app_name="ASFI/SCIP Monitor",
                app_icon=str(icon_path) if icon_path else None,
                timeout=duracion,
            )
            log.debug(f"Notificacion enviada via plyer ({duracion}s)")
            return True
        except Exception as exc:
            log.debug(f"plyer: {type(exc).__name__}")
            return False

    def metodo_ballontip():
        try:
            import subprocess

            duracion_ms = 15000 if urgente else 10000
            espera_s = 16 if urgente else 11
            if icon_path:
                icono_ps = str(icon_path).replace("'", "''")
                linea_icono = f"$n.Icon = New-Object System.Drawing.Icon('{icono_ps}')"
            else:
                linea_icono = "$n.Icon = [System.Drawing.SystemIcons]::Warning"
            ps_script = f"""
            Add-Type -AssemblyName System.Windows.Forms
            Add-Type -AssemblyName System.Drawing
            $n = New-Object System.Windows.Forms.NotifyIcon
            {linea_icono}
            $n.BalloonTipTitle = '{titulo_ps}'
            $n.BalloonTipText = '{mensaje_ps}'
            $n.Visible = $True
            $n.ShowBalloonTip({duracion_ms})
            Start-Sleep -Seconds {espera_s}
            $n.Visible = $False
            """
            subprocess.run(
                ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps_script],
                timeout=120,
                capture_output=True,
                text=True,
            )
            log.debug(f"Notificacion enviada via PowerShell BalloonTip ({duracion_ms}ms)")
            return True
        except Exception as exc:
            log.debug(f"PowerShell BalloonTip: {type(exc).__name__}")
            return False

    if urgente:
        log.info("[URGENTE] Enviando notificacion por un metodo disponible...")
        if metodo_ballontip() or metodo_plyer() or metodo_messagebox():
            return
    elif metodo_ballontip() or metodo_plyer() or metodo_messagebox():
        return
    log.warning(f"No se pudo mostrar notificacion: {titulo}")
