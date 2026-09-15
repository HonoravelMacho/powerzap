"""Conexão do WhatsApp via QR Code (Evolution API)."""
import threading
import time

import flet as ft

from powerzap import db
from powerzap.evolution import EvolutionAPI, EvolutionError


def get_api() -> EvolutionAPI:
    s = db.get_settings()
    return EvolutionAPI(s["evolution_url"], s["api_key"], s["instance"])


STATE_LABELS = {
    "open": ("Conectado", ft.colors.GREEN_400),
    "connecting": ("Conectando...", ft.colors.AMBER_400),
    "close": ("Desconectado", ft.colors.RED_400),
}


class ConnectView(ft.Column):
    def __init__(self, page):
        super().__init__(
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            alignment=ft.MainAxisAlignment.START,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
            spacing=16,
        )
        self.page_ref = page
        self.qr_image = ft.Image(visible=False, width=280, height=280)
        self.status_row = ft.Row([], alignment=ft.MainAxisAlignment.CENTER)

        title = ft.Text("Conexão do WhatsApp", size=22, weight=ft.FontWeight.BOLD)
        subtitle = ft.Text(
            "Gere o QR Code e escaneie pelo WhatsApp "
            "(Aparelhos conectados > Conectar aparelho).",
            color=ft.colors.with_opacity(0.6, ft.colors.WHITE), text_align=ft.TextAlign.CENTER,
        )

        gen_btn = ft.FilledButton(
            "Gerar QR Code", icon=ft.icons.QR_CODE_2, on_click=self.generate_qr
        )
        check_btn = ft.OutlinedButton(
            "Verificar status", icon=ft.icons.SYNC, on_click=self.check_status
        )
        logout_btn = ft.OutlinedButton(
            "Desconectar", icon=ft.icons.LINK_OFF,
            style=ft.ButtonStyle(color=ft.colors.RED_300),
            on_click=self.logout,
        )

        self.controls = [
            ft.Container(height=20),
            title, subtitle,
            ft.Container(self.status_row, height=36),
            self.qr_image,
            ft.Row([gen_btn, check_btn, logout_btn],
                   alignment=ft.MainAxisAlignment.CENTER, wrap=True),
        ]
        self.reload()
        threading.Thread(target=self._set_status, daemon=True).start()

    def reload(self):
        pass

    def _safe_update(self):
        for fn in (lambda: self.update(),):
            try:
                fn()
            except Exception:
                pass
        try:
            if getattr(self, "page_ref", None) is not None:
                self.page_ref.update()
        except Exception:
            pass

    def _set_status(self):
        self.status_row.controls = [ft.ProgressRing(18, stroke_width=3)]
        self._safe_update()
        try:
            api = get_api()
            state = api.connection_state()
            info = state.get("instance") if isinstance(state, dict) else None
            if not isinstance(info, dict):
                info = state if isinstance(state, dict) else {}
            raw = str(info.get("state", "desconhecido")).lower()
            label, color = STATE_LABELS.get(raw, (raw.capitalize(), ft.colors.GREY))
            if raw == "open":
                self.qr_image.visible = False
            self.status_row.controls = [
                ft.Icon(ft.icons.CIRCLE, size=12, color=color),
                ft.Text(f"Instância '{info.get('name', api.instance)}': {label}", weight=ft.FontWeight.BOLD),
            ]
        except EvolutionError as ex:
            self.status_row.controls = [
                ft.Icon(ft.icons.ERROR_OUTLINE, color=ft.colors.RED_400),
                ft.Text(str(ex), size=13),
            ]
        except Exception as ex:
            self.status_row.controls = [
                ft.Icon(ft.icons.ERROR_OUTLINE, color=ft.colors.RED_400),
                ft.Text(f"Erro: {ex}", size=13),
            ]
        self._safe_update()

    def generate_qr(self, e=None):
        self.qr_image.visible = True
        self.qr_image.src = None
        self.qr_image.src_base64 = None
        self.status_row.controls = [ft.ProgressRing(20)]
        self._safe_update()

        def task():
            try:
                api = get_api()
                try:
                    st = api.connection_state()
                    info = st.get("instance") if isinstance(st, dict) else st
                    state = str((info or {}).get("state", "")).lower()
                except Exception:
                    state = ""
                if state == "open":
                    self.qr_image.visible = False
                    self._set_status()
                    return
                try:
                    qr = api.connect_qr()
                except EvolutionError as ex1:
                    # Instância pode não existir (404) ou estar fechada.
                    # Tenta criar e conectar de novo; se já existe (409),
                    # ignora e tenta conectar mesmo assim.
                    msg1 = str(ex1)
                    try:
                        api.create_instance()
                    except EvolutionError as ex_create:
                        if "409" not in str(ex_create) and "already" not in str(ex_create).lower():
                            # Segue para segunda tentativa de QR de qualquer forma.
                            pass
                    try:
                        qr = api.connect_qr()
                    except EvolutionError as ex2:
                        raise EvolutionError(f"{msg1} | retry: {ex2}")
                self.qr_image.visible = True
                self.qr_image.src = None
                self.qr_image.src_base64 = qr.split(",", 1)[-1]
                self.status_row.controls = [
                    ft.Icon(ft.icons.QR_CODE_SCANNER, color=ft.colors.GREEN_400),
                    ft.Text("Escaneie o QR Code pelo WhatsApp..."),
                ]
                self._safe_update()
                self._poll_until_connected()
            except (EvolutionError, Exception) as ex:
                self.qr_image.visible = False
                self.status_row.controls = [
                    ft.Icon(ft.icons.ERROR_OUTLINE, color=ft.colors.RED_400),
                    ft.Expanded(ft.Text(str(ex), size=13)),
                ]
                self._safe_update()

        threading.Thread(target=task, daemon=True).start()

    def _poll_until_connected(self, max_attempts: int = 60):
        api = get_api()
        for _ in range(max_attempts):
            time.sleep(4)
            try:
                st = api.connection_state()
                info = st.get("instance") if isinstance(st, dict) else st
                if not isinstance(info, dict):
                    info = {}
                raw = str(info.get("state", "")).lower()
            except (EvolutionError, Exception):
                continue
            if raw == "open":
                self.qr_image.visible = False
                self._set_status()
                return
            if raw == "close":
                self.status_row.controls = [
                    ft.Icon(ft.icons.QR_CODE_2_OFF, color=ft.colors.RED_400),
                    ft.Text("QR Code expirou — clique em 'Gerar QR Code' novamente."),
                ]
                self._safe_update()
                return

    def check_status(self, e=None):
        threading.Thread(target=self._set_status, daemon=True).start()

    def logout(self, e=None):
        try:
            get_api().logout()
            self.qr_image.visible = False
            self.status_row.controls = [ft.Text("Instância desconectada.")]
        except EvolutionError as ex:
            self.status_row.controls = [ft.Text(str(ex), size=13)]
        except Exception as ex:
            self.status_row.controls = [ft.Text(f"Erro: {ex}", size=13)]
        self._safe_update()
