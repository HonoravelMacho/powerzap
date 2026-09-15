"""Configurações da integração com a Evolution API."""
import threading
from datetime import datetime

import flet as ft

from powerzap import db
from powerzap.views.connect_view import get_api
from powerzap.evolution import EvolutionError


class SettingsView(ft.Column):
    def __init__(self, page):
        super().__init__(
            expand=True, spacing=14, scroll=ft.ScrollMode.AUTO,
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        )
        self.page_ref = page
        s = db.get_settings()

        title = ft.Text("Ajustes", size=22, weight=ft.FontWeight.BOLD)

        self.url_field = ft.TextField(
            label="URL da Evolution API", value=s["evolution_url"],
            prefix_icon=ft.icons.LINK, expand=True,
        )
        self.key_field = ft.TextField(
            label="API Key (apikey)", value=s["api_key"],
            prefix_icon=ft.icons.KEY, password=True, can_reveal_password=True, expand=True,
        )
        self.instance_field = ft.TextField(
            label="Nome da instância", value=s["instance"],
            prefix_icon=ft.icons.ACCOUNT_CIRCLE_OUTLINED, width=300,
        )
        self.my_number_field = ft.TextField(
            label="Meu número (para mensagem de teste)", value=s.get("my_number", ""),
            hint_text="5511999999999",
            prefix_icon=ft.icons.PERSON, width=300,
        )
        self.test_result = ft.Container()
        self.scheduler_status = ft.Container()

        save_btn = ft.FilledButton(
            "Salvar configurações", icon=ft.icons.SAVE, on_click=self.save
        )
        test_btn = ft.OutlinedButton(
            "Testar conexão", icon=ft.icons.NETWORK_CHECK, on_click=self.test
        )
        detect_btn = ft.OutlinedButton(
            "Detectar meu número", icon=ft.icons.PERSON_SEARCH,
            on_click=self.detect_number,
        )

        info = ft.Container(
            padding=16, border_radius=12,
            bgcolor=ft.colors.with_opacity(0.06, ft.colors.WHITE),
            content=ft.Column([
                ft.Text("Como conectar", weight=ft.FontWeight.BOLD),
                ft.Text(
                    "1. Rode a Evolution API localmente (padrão http://localhost:8080).\n"
                    "2. Informe aqui a API Key definida na sua instalação.\n"
                    "3. Vá em 'Conexão' e escaneie o QR Code.\n"
                    "4. Agende mensagens no Calendário.",
                    size=13, color=ft.colors.with_opacity(0.7, ft.colors.WHITE),
                ),
            ], tight=True),
            width=520,
        )

        self.controls = [
            ft.Container(height=10), title,
            ft.Container(width=520, content=self.url_field),
            ft.Container(width=520, content=self.key_field),
            self.instance_field,
            self.my_number_field,
            ft.Row([save_btn, test_btn, detect_btn],
                   alignment=ft.MainAxisAlignment.CENTER, wrap=True),
            self.test_result,
            self.scheduler_status,
            info,
        ]
        self._refresh_scheduler_status()

    def reload(self):
        self._refresh_scheduler_status()

    def _refresh_scheduler_status(self):
        try:
            s = db.get_settings()
            last = s.get("scheduler_last_run", "")
            pendentes = len(db.list_pending(db._now()))
            if last:
                try:
                    dt = datetime.strptime(last, "%Y-%m-%d %H:%M:%S")
                    diff = (datetime.now() - dt).total_seconds()
                    if diff < 90:
                        msg = f"Agendador ativo (último ciclo há {int(diff)}s). {pendentes} pendente(s)."
                        color = ft.colors.GREEN_400
                        icon = ft.icons.CHECK_CIRCLE
                    else:
                        msg = (f"Agendador parece parado (último ciclo: {last}). "
                               "Rode: powerzap-scheduler --interval 20")
                        color = ft.colors.AMBER_400
                        icon = ft.icons.WARNING
                except ValueError:
                    msg, color, icon = f"Último ciclo: {last}", ft.colors.GREY, ft.icons.INFO
            else:
                msg = "Agendador ainda não rodou. Rode: powerzap-scheduler --interval 20"
                color, icon = ft.colors.AMBER_400, ft.icons.INFO
            self.scheduler_status.content = ft.Row([
                ft.Icon(icon, color=color), ft.Expanded(ft.Text(msg, size=13))])
        except Exception:
            pass

    def save(self, e=None):
        url = (self.url_field.value or "").strip()
        key = (self.key_field.value or "").strip()
        inst = (self.instance_field.value or "").strip() or "powerzap"
        my_num = db.normalize_number(self.my_number_field.value or "")
        db.set_setting("evolution_url", url)
        db.set_setting("api_key", key)
        db.set_setting("instance", inst)
        db.set_setting("my_number", my_num)
        self.page_ref.open(ft.SnackBar(ft.Text("Configurações salvas!")))

    def detect_number(self, e=None):
        threading.Thread(target=self._detect_async, daemon=True).start()

    def _detect_async(self):
        try:
            owner = get_api().fetch_owner_number()
        except Exception as ex:
            owner = None
            err = str(ex)
        else:
            err = ""
        if owner:
            self.my_number_field.value = owner
            db.set_setting("my_number", owner)
            msg = f"Seu número detectado: {owner}"
        else:
            msg = f"Não consegui detectar. Digite manualmente. {err}"[:200]
        self.page_ref.open(ft.SnackBar(ft.Text(msg)))
        try:
            self.update()
        except AssertionError:
            pass

    def test(self, e=None):
        self.save()
        self.test_result.content = ft.Row([
            ft.ProgressRing(16, stroke_width=3),
            ft.Text("Testando conexão..."),
        ])
        self.update()
        threading.Thread(target=self._test_async, daemon=True).start()

    def _test_async(self):
        try:
            api = get_api()
            api.connection_state()
            msg, color, icon = "Evolution API respondeu com sucesso!", ft.colors.GREEN_400, ft.icons.CHECK_CIRCLE
        except EvolutionError as ex:
            msg, color, icon = str(ex), ft.colors.RED_400, ft.icons.ERROR_OUTLINE
        except Exception as ex:
            msg, color, icon = f"Erro: {ex}", ft.colors.RED_400, ft.icons.ERROR_OUTLINE
        self.test_result.content = ft.Row([
            ft.Icon(icon, color=color), ft.Expanded(ft.Text(msg, size=13)),
        ])
        try:
            self.update()
        except AssertionError:
            pass
