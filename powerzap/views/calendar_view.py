"""Calendário interativo em tela cheia com CRUD de mensagens."""
import calendar as pycal
import mimetypes
import os
import shutil
import threading
import uuid
from datetime import date, datetime, timedelta

import flet as ft

from powerzap import db
from powerzap.evolution import detect_media_type
from powerzap.views.connect_view import get_api

STATUS_COLORS = {
    "pendente": ft.colors.AMBER_400,
    "enviada": ft.colors.GREEN_400,
    "falhou": ft.colors.RED_400,
}

PRESET_HOURS = ["08:00", "09:00", "10:00", "12:00", "14:00", "16:00", "18:00", "20:00"]

WEEKDAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"]


class MessageDialog(ft.AlertDialog):
    """Diálogo único com dois painéis: formulário e lista de contatos."""

    def __init__(self, page, on_done, message=None, default_day=None):
        super().__init__(modal=True)
        self.page_ref = page
        self.on_done = on_done
        self.message = message
        self.picker_contacts = []
        # Anexo: caminho temporário selecionado + caminho já salvo (edição)
        self.pending_file: str | None = None
        self.saved_media_path: str | None = (message or {}).get("media_path")
        self.saved_media_type: str | None = (message or {}).get("media_type")
        self.file_picker = ft.FilePicker(on_result=self._on_file_picked)
        try:
            page.overlay.append(self.file_picker)
        except Exception:
            pass

        # ---------- Formulário ----------
        self.number_field = ft.TextField(
            label="Número do WhatsApp",
            hint_text="5511999999999",
            width=280,
        )
        pick_contact_btn = ft.IconButton(
            icon=ft.icons.CONTACTS,
            tooltip="Selecionar contato da lista",
            icon_color=ft.colors.GREEN_400,
            on_click=lambda e: self._show_picker(),
        )
        self.text_field = ft.TextField(
            label="Mensagem / legenda (opcional se tiver anexo)",
            multiline=True, min_lines=3, max_lines=6, expand=True,
        )
        self.date_field = ft.TextField(
            label="Data", width=140, hint_text="AAAA-MM-DD",
            value=(default_day or date.today().isoformat()),
        )
        self.time_field = ft.TextField(label="Hora", width=100, value="09:00")
        if message:
            self.number_field.value = message["number"]
            self.text_field.value = message.get("caption") or message["text"]
            dt = message["scheduled_at"]
            self.date_field.value = dt[:10]
            self.time_field.value = dt[11:16]

        self.tag_dropdown = ft.Dropdown(
            label="Etiqueta", width=200,
            options=[ft.dropdown.Option(key="", text="Sem etiqueta")]
            + [
                ft.dropdown.Option(key=str(t["id"]), text=t["name"])
                for t in db.list_tags()
            ],
            value=str(message["tag_id"]) if message and message["tag_id"] else "",
        )

        # Modelos rápidos
        templates = db.list_templates()
        self.template_dropdown = ft.Dropdown(
            label="Modelo rápido", width=280,
            options=[ft.dropdown.Option(key=str(t["id"]), text=t["title"])
                     for t in templates],
        )

        # Anexo (imagem / PDF / etc)
        self.file_label = ft.Text(
            self._media_display_name() or "Nenhum arquivo anexado",
            size=12, color=ft.colors.with_opacity(0.65, ft.colors.WHITE),
        )
        self.file_preview = ft.Image(
            width=220, height=140, fit=ft.ImageFit.CONTAIN, visible=False,
        )
        self._refresh_preview()
        attach_btn = ft.OutlinedButton(
            "Anexar arquivo (qualquer tipo: PDF, imagem, vídeo...)",
            icon=ft.icons.ATTACH_FILE,
            # Sem allowed_extensions: aceita qualquer arquivo (PDF etc).
            on_click=lambda e: self.file_picker.pick_files(
                allow_multiple=False,
            ),
        )
        remove_btn = ft.IconButton(
            icon=ft.icons.CLOSE, tooltip="Remover anexo",
            on_click=lambda e: self._clear_file(),
        )

        # Recorrência simples
        rec_value = (message or {}).get("recurrence") or "none"
        if rec_value not in ("none", "daily", "weekly"):
            rec_value = "none"
        self.rec_dropdown = ft.Dropdown(
            label="Repetir", width=160,
            options=[
                ft.dropdown.Option(key="none", text="Não repetir"),
                ft.dropdown.Option(key="daily", text="Diária"),
                ft.dropdown.Option(key="weekly", text="Semanal"),
            ],
            value=rec_value,
        )
        self.rec_end_field = ft.TextField(
            label="Repetir até (AAAA-MM-DD)", width=200, hint_text="opcional",
            value=(message or {}).get("recurrence_end") or "",
        )

        quick_hours = ft.Row(
            [
                ft.TextButton(h, on_click=lambda e, h=h: self._set_hour(h))
                for h in PRESET_HOURS
            ],
            wrap=True, spacing=4,
        )

        self.form_area = ft.Column([
            ft.Row([self.number_field, pick_contact_btn], spacing=6),
            ft.Row([self.template_dropdown,
                    ft.TextButton("Usar", on_click=lambda e: self._apply_template())],
                   spacing=6),
            self.text_field,
            ft.Row([attach_btn, remove_btn], spacing=4),
            self.file_label,
            self.file_preview,
            ft.Row([self.date_field, self.time_field]),
            quick_hours,
            ft.Row([self.tag_dropdown, self.rec_dropdown], spacing=8),
            self.rec_end_field,
        ], tight=True, spacing=12, visible=True, scroll=ft.ScrollMode.AUTO)

        # ---------- Seletor de contatos (mesmo diálogo, sem empilhar) ----------
        self.picker_kind = "all"
        self.search_field = ft.TextField(
            label="Buscar por nome ou número...",
            prefix_icon=ft.icons.SEARCH,
            on_change=lambda e: self._filter(),
            border_radius=10,
        )
        self.picker_status = ft.Text("", size=12,
                                     color=ft.colors.with_opacity(0.6, ft.colors.WHITE))
        self.contact_list = ft.ListView(height=380, spacing=4)

        back_btn = ft.IconButton(
            icon=ft.icons.ARROW_BACK,
            tooltip="Voltar ao formulário",
            on_click=lambda e: self._show_form(),
        )
        sync_btn = ft.IconButton(
            icon=ft.icons.SYNC, tooltip="Atualizar da API",
            on_click=lambda e: threading.Thread(
                target=self._sync_from_api, daemon=True
            ).start(),
        )
        self.kind_all = ft.TextButton("Todos", on_click=lambda e: self._set_kind("all"))
        self.kind_contacts = ft.TextButton("Contatos", on_click=lambda e: self._set_kind("contacts"))
        self.kind_groups = ft.TextButton("Grupos", on_click=lambda e: self._set_kind("groups"))
        self.me_btn = ft.OutlinedButton(
            "Usar meu número", icon=ft.icons.PERSON,
            on_click=lambda e: threading.Thread(
                target=self._pick_own_number, daemon=True).start(),
        )

        self.picker_area = ft.Column([
            ft.Row([
                back_btn,
                ft.Text("Selecionar destino", size=18, weight=ft.FontWeight.BOLD),
                ft.Container(expand=True),
                sync_btn,
            ]),
            ft.Row([self.kind_all, self.kind_contacts, self.kind_groups,
                    ft.Container(expand=True), self.me_btn],
                   spacing=4, wrap=True),
            self.search_field,
            self.contact_list,
            self.picker_status,
        ], tight=False, spacing=10, visible=False)

        # ---------- Ações ----------
        self.actions = [
            ft.TextButton("Cancelar", on_click=lambda e: self._close()),
            ft.FilledButton("Salvar", icon=ft.icons.SAVE, on_click=lambda e: self._save()),
        ]
        if message:
            self.actions.insert(
                0,
                ft.TextButton(
                    "Excluir", icon=ft.icons.DELETE_OUTLINE, style=ft.ButtonStyle(color=ft.colors.RED_300),
                    on_click=lambda e: self._delete(),
                ),
            )

        self.content = ft.Container(
            width=560,
            content=ft.Column([
                ft.Text("Editar mensagem" if message else "Nova mensagem agendada",
                        size=18, weight=ft.FontWeight.BOLD),
                self.form_area,
                self.picker_area,
            ], tight=True, spacing=14),
        )

    # ----- navegação entre painéis do diálogo -----

    def _safe_refresh(self):
        """Atualiza a UI a partir de qualquer thread, sem nunca travar."""
        for fn in (
            lambda: self.contact_list.update(),
            lambda: self.picker_status.update(),
            lambda: self.update(),
        ):
            try:
                fn()
            except Exception:
                pass
        try:
            if getattr(self, "page_ref", None) is not None:
                self.page_ref.update()
        except Exception:
            pass

    def _show_picker(self):
        self.picker_area.visible = True
        self.form_area.visible = False
        for a in self.actions:          # esconde todos os botões na listagem
            a.visible = False
        try:
            self._load_contacts()
        except Exception as ex:
            self.picker_contacts = []
            try:
                self._render_list(
                    placeholder_override=f"Erro ao ler cache: {ex}")
            except Exception:
                pass
        self._safe_refresh()
        if db.count_contacts() == 0:
            self._set_status("Sincronizando contatos da Evolution API...")
            self._safe_refresh()
        threading.Thread(target=self._sync_from_api, daemon=True).start()

    def _show_form(self):
        self.picker_area.visible = False
        self.form_area.visible = True
        for a in self.actions:
            a.visible = True
        self._safe_refresh()

    # ----- contatos e grupos -----

    def _load_contacts(self):
        try:
            all_contacts = db.list_contacts()
        except Exception as ex:
            self.picker_contacts = []
            self._render_list(
                placeholder_override=f"Erro no banco local: {ex}")
            self._set_status(f"Erro no banco local: {ex}")
            return
        self.picker_contacts = db.filter_local(
            all_contacts, self.search_field.value or "", self.picker_kind)
        self._render_list()
        try:
            total = db.count_contacts()
            groups = db.count_groups()
        except Exception:
            total, groups = len(all_contacts), 0
        self._set_status(f"{total} salvo(s) • {groups} grupo(s) em cache.")

    def _set_status(self, msg: str):
        try:
            self.picker_status.value = msg
        except Exception:
            pass
        self._safe_refresh()

    def _set_kind(self, kind: str):
        self.picker_kind = kind
        self._filter()
        self._safe_refresh()

    def _sync_from_api(self):
        self._set_status("Sincronizando com a Evolution API...")
        try:
            api = get_api()
            # Diagnóstico rápido de conexão antes de buscar contatos.
            try:
                connected = api.is_connected()
            except Exception:
                connected = False
            # Busca cada fonte separada para mostrar contagem real
            # (contatos / conversas / grupos) em vez de esconder falha.
            try:
                contacts = api.find_contacts()
            except Exception:
                contacts = []
            try:
                chats = api.find_chats()
            except Exception:
                chats = []
            try:
                groups = api.fetch_groups()
            except Exception:
                groups = []
            try:
                owner = api.fetch_owner_number()
            except Exception:
                owner = None
            merged: dict = {}
            for ct in (contacts or []) + (chats or []) + (groups or []):
                if not isinstance(ct, dict) or not ct.get("number"):
                    continue
                key = ct["number"]
                if key not in merged or (not merged[key]["name"] and ct["name"]):
                    merged[key] = ct
            if owner and owner not in merged:
                merged[owner] = {"number": owner, "name": "Você (este número)",
                                 "is_group": False}
            try:
                saved_raw = (db.get_settings().get("my_number") or "").strip()
                saved = db.normalize_number(saved_raw) if saved_raw else ""
            except Exception:
                saved = ""
            if saved and saved not in merged:
                merged[saved] = {"number": saved, "name": "Meu número",
                                 "is_group": False}
            fresh = list(merged.values())
            dbg = (f"C:{len(contacts or [])} Ch:{len(chats or [])} "
                   f"G:{len(groups or [])} dono:{owner or '-'}")
            if fresh:
                try:
                    db.replace_contacts(fresh)
                except Exception as ex:
                    self._set_status(f"Erro ao salvar cache: {ex}")
                    return
            else:
                # API voltou vazia logo após QR: mantém cache e avisa.
                try:
                    cached = db.list_contacts()
                except Exception:
                    cached = []
                if cached:
                    self.picker_contacts = db.filter_local(
                        cached, (self.search_field.value or "").strip(),
                        self.picker_kind)
                    self._render_list()
                    extra = "" if connected else " WhatsApp parece desconectado."
                    self._set_status(
                        f"API vazia ({dbg})... mostrando cache. "
                        f"Toque em sincronizar de novo.{extra}")
                    return
            # Se descobriu o dono, salva como sugestão de "meu número"
            try:
                if owner:
                    s = db.get_settings()
                    if not (s.get("my_number") or "").strip():
                        db.set_setting("my_number", owner)
            except Exception:
                pass
            # Garante "meu número" salvo mesmo sem detecção automática.
            try:
                if saved:
                    local = db.list_contacts()
                    if saved and not any(c["number"] == saved for c in local):
                        db.replace_contacts(
                            local + [{"number": saved, "name": "Meu número",
                                      "is_group": False}]
                        )
            except Exception:
                pass
            try:
                all_contacts = db.list_contacts()
            except Exception as ex:
                self._set_status(f"Erro ao ler cache: {ex}")
                return
            self.picker_contacts = db.filter_local(
                all_contacts, (self.search_field.value or "").strip(),
                self.picker_kind)
            self._render_list()
            try:
                n_groups = sum(1 for c in all_contacts if c.get("is_group"))
                total = len(all_contacts)
            except Exception:
                total, n_groups = 0, 0
            if total == 0:
                s = db.get_settings()
                detail = (
                    f"URL: {s.get('evolution_url')} | "
                    f"Instância: {s.get('instance')} | "
                    f"Conectado: {'sim' if connected else 'não'} | {dbg}"
                )
                self._render_list(
                    placeholder_override=(
                        "Nenhum contato retornado pela API.\n"
                        f"{detail}\n"
                        "1) Abra a aba 'Conexão' e confirme 'Conectado'.\n"
                        "2) Em 'Ajustes', confira URL, API Key e instância.\n"
                        "3) Toque em sincronizar de novo."
                    ))
                self._set_status(
                    "Nenhum contato ainda. " + detail)
            else:
                self._set_status(f"{total} sincronizados ({n_groups} grupos) | {dbg}.")
        except Exception as ex:
            try:
                self._render_list(
                    placeholder_override=f"Erro inesperado: {ex}")
            except Exception:
                pass
            self._set_status(f"Sem conexão com a API — usando cache ({ex}).")

    def _filter(self):
        try:
            query = (self.search_field.value or "").strip().lower()
            self.picker_contacts = db.filter_local(
                db.list_contacts(), query, self.picker_kind)
            self._render_list()
        except Exception as ex:
            try:
                self._render_list(
                    placeholder_override=f"Erro ao filtrar: {ex}")
            except Exception:
                pass

    def _pick_own_number(self):
        self._set_status("Detectando seu número...")
        try:
            owner = get_api().fetch_owner_number()
        except Exception:
            owner = None
        if not owner:
            owner = (db.get_settings().get("my_number") or "").strip()
        if not owner:
            self._set_status("Não achei seu número. Configure em Ajustes > Meu número.")
            return
        self.number_field.value = owner
        self._set_status(f"Seu número: {owner}")
        self._show_form()
        try:
            self.text_field.focus()
        except Exception:
            pass

    def _render_list(self, placeholder_override: str | None = None):
        rows = []
        try:
            contacts = list(self.picker_contacts or [])[:300]
        except Exception:
            contacts = []
        for ct in contacts:
            try:
                if not isinstance(ct, dict) or not ct.get("number"):
                    continue
                name = ct.get("name") or "(sem nome)"
                is_group = bool(ct.get("is_group"))
                icon = ft.icons.GROUPS if is_group else ft.icons.PERSON_OUTLINE
                badge = "Grupo" if is_group else "Contato"
                rows.append(
                    ft.ListTile(
                        leading=ft.Icon(icon, color=ft.colors.GREEN_300),
                        title=ft.Text(f"{name} • {badge}", weight=ft.FontWeight.W_600),
                        subtitle=ft.Text(str(ct.get("number")), size=12,
                                         color=ft.colors.with_opacity(0.55, ft.colors.WHITE)),
                        dense=True,
                        on_click=lambda e, c=dict(ct): self._pick(c),
                    )
                )
            except Exception:
                continue
        if not rows:
            msg = placeholder_override or (
                "Nada por aqui.\n"
                "1) Confira a aba 'Conexão' (precisa estar Conectado).\n"
                "2) Em 'Ajustes', salve seu número em 'Meu número'.\n"
                "3) Para testar sem lista: volte, digite o número manualmente "
                "no campo Número e salve — não depende do seletor."
            )
            rows.append(ft.Container(
                padding=30,
                content=ft.Column([
                    ft.Icon(ft.icons.PHONE_DISABLED_OUTLINED, size=36,
                            color=ft.colors.with_opacity(0.35, ft.colors.WHITE)),
                    ft.Text(
                        msg,
                        size=12, text_align=ft.TextAlign.CENTER,
                        color=ft.colors.with_opacity(0.55, ft.colors.WHITE),
                    ),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            ))
        try:
            self.contact_list.controls = rows
        except Exception:
            return
        self._safe_refresh()

    def _pick(self, contact: dict):
        self.number_field.value = contact["number"]
        self._show_form()
        try:
            self.text_field.focus()
        except Exception:
            pass

    # ----- formulário -----

    def _set_hour(self, h):
        self.time_field.value = h
        try:
            self.update()
        except Exception:
            pass

    def _close(self):
        self.page_ref.close(self)

    def _parse_dt(self) -> str | None:
        try:
            dt = datetime.strptime(
                f"{self.date_field.value} {self.time_field.value}", "%Y-%m-%d %H:%M"
            )
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    # ----- anexo -----
    def _media_display_name(self) -> str | None:
        path = self.pending_file or self.saved_media_path
        if not path:
            return None
        return os.path.basename(path)

    def _refresh_preview(self):
        try:
            path = self.pending_file or self.saved_media_path
            name = os.path.basename(path) if path else "Nenhum arquivo anexado"
            self.file_label.value = name if path else "Nenhum arquivo anexado"
            is_image = path and os.path.splitext(path)[1].lower() in (
                ".png", ".jpg", ".jpeg", ".gif", ".webp")
            if is_image and os.path.exists(path):
                self.file_preview.src = path
                self.file_preview.visible = True
            else:
                self.file_preview.visible = False
            self.file_label.update()
            self.file_preview.update()
        except Exception:
            pass

    def _on_file_picked(self, e: ft.FilePickerResultEvent):
        if not e.files:
            return
        picked = e.files[0]
        # Flet Web/desktop: path pode vir vazio; usa path se disponível
        src = picked.path or picked.name
        if picked.path and os.path.exists(picked.path):
            size = os.path.getsize(picked.path)
            if size > db.MAX_MEDIA_BYTES:
                self.page_ref.open(ft.SnackBar(
                    ft.Text("Arquivo maior que 16MB, limite do WhatsApp.")))
                return
            self.pending_file = picked.path
        else:
            # Sem caminho local acessível: mantém só o nome para avisar
            self.page_ref.open(ft.SnackBar(
                ft.Text("Não foi possível ler o arquivo local. Tente outro arquivo.")))
            return
        self._refresh_preview()

    def _clear_file(self):
        self.pending_file = None
        self.saved_media_path = None
        self.saved_media_type = None
        self._refresh_preview()

    def _apply_template(self):
        tid = self.template_dropdown.value
        if not tid:
            return
        for t in db.list_templates():
            if str(t["id"]) == str(tid):
                self.text_field.value = t["body"]
                try:
                    self.update()
                except Exception:
                    pass
                break

    def _store_attachment(self) -> tuple[str | None, str | None, str | None]:
        """Copia anexo para a pasta de mídia e retorna (path, type, mimetype)."""
        if self.pending_file:
            src = self.pending_file
            if not os.path.exists(src):
                return None, None, None
            dest_dir = db.media_dir()
            safe_name = f"{uuid.uuid4().hex}_{os.path.basename(src)}"
            dest = os.path.join(dest_dir, safe_name)
            shutil.copy2(src, dest)
            mtype = detect_media_type(dest)
            mime, _ = mimetypes.guess_type(dest)
            return dest, mtype, mime or "application/octet-stream"
        if self.saved_media_path:
            return self.saved_media_path, self.saved_media_type, \
                (self.message or {}).get("mimetype")
        return None, None, None

    def _save(self):
        dt = self._parse_dt()
        text_val = (self.text_field.value or "").strip()
        has_file = bool(self.pending_file or self.saved_media_path)
        number_val = db.normalize_number(self.number_field.value or "")
        if not number_val or (not text_val and not has_file) or not dt:
            self.page_ref.open(
                ft.SnackBar(ft.Text("Preencha número, mensagem ou anexo, e data/hora válidas."))
            )
            return
        # Valida recorrência
        rec = self.rec_dropdown.value or "none"
        rec_end = (self.rec_end_field.value or "").strip() or None
        if rec_end:
            try:
                datetime.strptime(rec_end, "%Y-%m-%d")
            except ValueError:
                self.page_ref.open(ft.SnackBar(
                    ft.Text("Data de 'Repetir até' inválida. Use AAAA-MM-DD ou deixe vazio.")))
                return
        tag_id = int(self.tag_dropdown.value) if self.tag_dropdown.value else None
        try:
            media_path, media_type, mimetype = self._store_attachment()
        except Exception as ex:
            self.page_ref.open(ft.SnackBar(ft.Text(f"Falha ao salvar anexo: {ex}")))
            return
        caption = text_val or None
        if self.message:
            db.update_message(self.message["id"], number_val,
                              text_val, dt, tag_id,
                              media_path=media_path, media_type=media_type,
                              caption=caption, mimetype=mimetype,
                              recurrence=rec, recurrence_end=rec_end)
        else:
            db.create_message(number_val, text_val, dt, tag_id,
                              media_path=media_path, media_type=media_type,
                              caption=caption, mimetype=mimetype,
                              recurrence=rec, recurrence_end=rec_end)
        self._close()
        self.on_done()

    def _delete(self):
        db.delete_message(self.message["id"])
        self._close()
        self.on_done()


class CalendarView(ft.Column):
    def __init__(self, page, on_change=None):
        super().__init__(expand=True, spacing=0)
        self.page_ref = page
        self.external_on_change = on_change or (lambda: None)
        today = date.today()
        self.year, self.month = today.year, today.month
        self.selected_date: date = today
        self.search_text: str = ""
        self.month_label = ft.Text(size=22, weight=ft.FontWeight.BOLD, width=190,
                                   text_align=ft.TextAlign.CENTER)

        prev_btn = ft.IconButton(ft.icons.CHEVRON_LEFT, tooltip="Mês anterior",
                                 on_click=lambda e: self._move(-1))
        next_btn = ft.IconButton(ft.icons.CHEVRON_RIGHT, tooltip="Próximo mês",
                                 on_click=lambda e: self._move(1))
        today_btn = ft.OutlinedButton("Hoje", icon=ft.icons.EVENT_AVAILABLE,
                                      on_click=lambda e: self._go_today())
        new_btn = ft.FilledButton(
            "Nova mensagem", icon=ft.icons.ADD, on_click=lambda e: self.open_dialog()
        )

        header = ft.Container(
            padding=ft.padding.only(left=20, right=20, top=12, bottom=12),
            content=ft.Row([
                prev_btn,
                self.month_label,
                next_btn,
                today_btn,
                ft.VerticalDivider(width=10),
                new_btn,
                ft.Container(expand=True),
                self._legend(),
            ]),
        )

        self.grid_area = ft.Column(spacing=8, expand=True)
        self.detail_area = ft.Container(
            bgcolor=ft.colors.with_opacity(0.04, ft.colors.WHITE),
            border_radius=12, padding=16,
            width=340,
        )

        body = ft.Row(
            [ft.Container(content=self.grid_area, expand=True,
                          padding=ft.padding.only(left=20, bottom=16)),
             self.detail_area],
            spacing=16, expand=True,
        )
        self.controls = [header, ft.Divider(height=1), body]
        self.reload()

    # ---------------- cabeçalho ----------------

    def _legend(self):
        row = []
        for name, color in STATUS_COLORS.items():
            row.append(ft.Container(width=12, height=12, border_radius=6, bgcolor=color))
            row.append(ft.Text(name, size=13))
        return ft.Row(row, spacing=6, wrap=True)

    def reload(self):
        self._build_grid()
        self._build_detail()
        if hasattr(self, "page") and self.page:
            try:
                self.update()
            except Exception:
                pass

    def _move(self, delta):
        m = self.month + delta
        if m < 1:
            self.year, self.month = self.year - 1, 12
        elif m > 12:
            self.year, self.month = self.year + 1, 1
        else:
            self.month = m
        if self.selected_date.month != self.month or self.selected_date.year != self.year:
            self.selected_date = date(self.year, self.month, 1)
        self.reload()

    def _go_today(self):
        t = date.today()
        self.year, self.month = t.year, t.month
        self.selected_date = t
        self.reload()

    # ---------------- grade ----------------

    def _counts_by_day(self) -> dict:
        counts = {}
        for msg in db.list_messages():
            d = msg["scheduled_at"][:10]
            counts.setdefault(d, []).append(msg)
        return counts

    def _month_matrix(self) -> list[list[date]]:
        """Matriz 6x7 sempre completa, começando na segunda-feira."""
        first = date(self.year, self.month, 1)
        start = first - timedelta(days=first.weekday())
        return [
            [start + timedelta(days=w * 7 + d) for d in range(7)]
            for w in range(6)
        ]

    def _build_cell(self, d: date, msgs: list) -> ft.Control:
        in_month = d.month == self.month
        is_today = d == date.today()
        is_selected = d == self.selected_date

        if is_selected:
            bg, fg, day_weight = ft.colors.GREEN_700, ft.colors.WHITE, ft.FontWeight.BOLD
        elif is_today:
            bg, fg, day_weight = (
                ft.colors.with_opacity(0.35, ft.colors.BLUE_GREY_700),
                ft.colors.WHITE, ft.FontWeight.BOLD,
            )
        else:
            bg, fg, day_weight = (
                ft.colors.with_opacity(0.05, ft.colors.WHITE), None, None
            )

        badges = ft.Row([
            ft.Container(width=7, height=7, border_radius=3,
                         bgcolor=STATUS_COLORS.get(m["status"], ft.colors.GREY))
            for m in msgs[:9]
        ], spacing=3, alignment=ft.MainAxisAlignment.CENTER)

        cell = ft.Container(
            bgcolor=bg,
            border_radius=12,
            border=ft.border.all(1, ft.colors.with_opacity(0.08, ft.colors.WHITE)),
            ink=True,
            on_click=lambda e, dd=d: self._select_day(dd),
            on_hover=self._hover_cell,
            data={"base": bg, "ativa": in_month},
            padding=8,
            alignment=ft.alignment.center,
            content=ft.Column([
                ft.Text(str(d.day), color=fg, weight=day_weight, size=15),
                badges,
            ], horizontal_alignment=ft.CrossAxisAlignment.CENTER,
               alignment=ft.MainAxisAlignment.CENTER, tight=True),
        )
        if not in_month:
            cell.opacity = 0.28
        cell.tooltip = d.strftime("%d/%m/%Y")
        return cell

    @staticmethod
    def _hover_cell(e: ft.ControlEvent):
        c: ft.Container = e.control
        info = c.data or {}
        if not info.get("ativa"):
            return
        if e.data == "true" and c.bgcolor != ft.colors.GREEN_700:
            c.bgcolor = ft.colors.with_opacity(0.12, ft.colors.WHITE)
        elif e.data != "true":
            c.bgcolor = info.get("base")
        try:
            c.update()
        except Exception:
            pass

    def _build_grid(self):
        self.month_label.value = f"{pycal.month_name[self.month].capitalize()} {self.year}"
        days_header = ft.Row([
            ft.Container(ft.Text(d, weight=ft.FontWeight.BOLD, size=13,
                                 color=ft.colors.with_opacity(0.6, ft.colors.WHITE)),
                         alignment=ft.alignment.center, expand=True)
            for d in WEEKDAYS
        ], spacing=8)

        by_day = self._counts_by_day()
        weeks = self._month_matrix()
        rows = [days_header]
        for week in weeks:
            cells = []
            for d in week:
                key = d.isoformat()
                cells.append(ft.Container(
                    content=self._build_cell(d, by_day.get(key, [])),
                    expand=True,
                ))
            rows.append(ft.Row(cells, spacing=8, expand=True))
        self.grid_area.controls = rows

    def _select_day(self, d: date):
        if d.month != self.month or d.year != self.year:
            self.year, self.month = d.year, d.month
        self.selected_date = d
        self.reload()

    # ---------------- painel lateral ----------------

    def _build_detail(self):
        key = self.selected_date.isoformat()
        msgs = sorted(db.list_messages(day=key, search=self.search_text),
                      key=lambda m: m["scheduled_at"])
        header = ft.Row([
            ft.Text(self.selected_date.strftime("%d/%m"), size=17,
                    weight=ft.FontWeight.BOLD),
            ft.Container(expand=True),
            ft.IconButton(ft.icons.ADD_CIRCLE_OUTLINE, tooltip="Nova mensagem",
                          on_click=lambda e: self.open_dialog(default_day=key)),
        ])
        search = ft.TextField(
            label="Buscar neste dia...", prefix_icon=ft.icons.SEARCH,
            value=self.search_text, dense=True,
            on_change=lambda e: self._on_search(e.control.value),
        )
        cards: list = [header, search]
        if not msgs:
            cards.append(ft.Container(
                padding=30,
                content=ft.Column([
                    ft.Icon(ft.icons.EVENT_NOTE_OUTLINED, size=40,
                            color=ft.colors.with_opacity(0.4, ft.colors.WHITE)),
                    ft.Text("Nenhuma mensagem neste dia",
                            color=ft.colors.with_opacity(0.5, ft.colors.WHITE)),
                ], horizontal_alignment=ft.CrossAxisAlignment.CENTER),
            ))
        for m in msgs:
            tag_chip = (
                ft.Container(
                    padding=ft.padding.symmetric(horizontal=8, vertical=2),
                    border_radius=8,
                    bgcolor=m["tag_color"],
                    content=ft.Text(m["tag_name"], size=11, color=ft.colors.BLACK),
                ) if m["tag_name"] else ft.Container()
            )
            body_text = m.get("caption") or m.get("text") or "(anexo)"
            attach_icon = (
                ft.Icon(ft.icons.ATTACH_FILE, size=16,
                        color=ft.colors.with_opacity(0.7, ft.colors.WHITE))
                if m.get("media_path") else ft.Container()
            )
            rec_icon = (
                ft.Icon(ft.icons.REPEAT, size=14,
                        color=ft.colors.with_opacity(0.6, ft.colors.WHITE))
                if (m.get("recurrence") or "none") != "none" else ft.Container()
            )
            error_text = (
                ft.Text(f"Erro: {m['error']}", size=11, color=ft.colors.RED_300)
                if m.get("status") == "falhou" and m.get("error") else ft.Container()
            )
            action_row: list = []
            if m.get("status") == "falhou":
                action_row.append(ft.TextButton(
                    "Tentar de novo", icon=ft.icons.REFRESH,
                    on_click=lambda e, mid=m["id"]: self._retry(mid)))
            action_row.append(ft.IconButton(
                ft.icons.COPY, icon_size=18, tooltip="Duplicar para amanhã",
                on_click=lambda e, mid=m["id"]: self._duplicate(mid)))
            card = ft.Container(
                margin=ft.margin.only(top=8),
                padding=12,
                border_radius=10,
                bgcolor=ft.colors.with_opacity(0.06, ft.colors.WHITE),
                ink=True,
                on_click=lambda e, msg=m: self.open_dialog(message=msg),
                content=ft.Column([
                    ft.Row([
                        ft.Text(m["scheduled_at"][11:16], weight=ft.FontWeight.BOLD,
                                size=16),
                        tag_chip,
                        attach_icon,
                        rec_icon,
                        ft.Container(expand=True),
                        ft.Container(
                            width=10, height=10, border_radius=5,
                            bgcolor=STATUS_COLORS.get(m["status"], ft.colors.GREY),
                        ),
                    ]),
                    ft.Text(body_text, size=13, max_lines=2,
                            overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(f"→ {m['number']} • {m['status']}", size=12,
                            color=ft.colors.with_opacity(0.55, ft.colors.WHITE)),
                    error_text,
                    ft.Row(action_row, spacing=0),
                ], spacing=4),
            )
            cards.append(card)
        self.detail_area.content = ft.ListView(cards, expand=True, spacing=0)

    def _on_search(self, value: str):
        self.search_text = value or ""
        self._build_detail()
        try:
            self.detail_area.update()
        except Exception:
            pass

    def _retry(self, msg_id: int):
        db.retry_message(msg_id)
        self.reload()

    def _duplicate(self, msg_id: int):
        db.duplicate_message(msg_id)
        self.reload()

    def open_dialog(self, e=None, message=None, default_day=None):
        dlg = MessageDialog(
            self.page_ref, on_done=self._after_change,
            message=message, default_day=default_day,
        )
        self.page_ref.open(dlg)

    def _after_change(self):
        self.reload()
        self.external_on_change()
