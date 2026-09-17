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


def _log(msg: str):
    try:
        from powerzap import crashlog as _cl
        _cl.debug(msg)
    except Exception:
        pass


class ContactPickerView(ft.Column):
    """Tela cheia para escolher contato/grupo. É um Column normal (como o
    CalendarView), atribuído a container.content — renderização confiável."""

    def __init__(self, page_ref, on_pick, on_back):
        super().__init__(expand=True, spacing=0)
        self.page_ref = page_ref
        self.on_pick_cb = on_pick
        self.on_back_cb = on_back
        self.picker_contacts: list = []
        self.picker_kind = "all"

        self.search = ft.TextField(
            label="Buscar por nome ou número...",
            prefix_icon=ft.icons.SEARCH,
            border_radius=10,
            on_change=lambda e: self._filter(),
        )
        self.status = ft.Text("Carregando...", size=12)
        self.diag = ft.Text("", size=10,
                            color=ft.colors.with_opacity(0.5, ft.colors.WHITE))
        self.list_info = ft.Text("", size=12,
                                 color=ft.colors.CYAN_200)

        self.kind_all = ft.TextButton(
            "Todos", on_click=lambda e: self._set_kind("all"))
        self.kind_contacts = ft.TextButton(
            "Contatos", on_click=lambda e: self._set_kind("contacts"))
        self.kind_groups = ft.TextButton(
            "Grupos", on_click=lambda e: self._set_kind("groups"))

        back_btn = ft.IconButton(
            ft.icons.ARROW_BACK, icon_size=28,
            tooltip="Voltar ao formulário", on_click=lambda e: self._finish(None))
        header = ft.Row([
            back_btn,
            ft.Text("Selecionar destino", size=22, weight=ft.FontWeight.BOLD),
            ft.Container(expand=True),
        ], vertical_alignment=ft.CrossAxisAlignment.CENTER)

        kind_row = ft.Row([
            self.kind_all, self.kind_contacts, self.kind_groups,
            ft.Container(expand=True),
            ft.OutlinedButton("Você (meu número)", icon=ft.icons.PERSON,
                              on_click=lambda e: self._pick_own()),
        ], spacing=4, wrap=True)

        self.list_box = ft.Container(
            content=ft.Text("Carregando...",
                            color=ft.colors.with_opacity(0.6, ft.colors.WHITE)),
            expand=True,
            border=ft.border.all(2, ft.colors.GREEN_400),
            border_radius=10, padding=8,
            bgcolor=ft.colors.SURFACE,
        )
        bottom = ft.Row([
            ft.FilledButton("Sincronizar API", icon=ft.icons.SYNC,
                            on_click=lambda e: self._sync()),
            ft.FilledButton("Recarregar cache", icon=ft.icons.REFRESH,
                            on_click=lambda e: self._load_cache()),
            ft.Container(expand=True),
        ], spacing=8)

        self.controls = [
            ft.Container(
                bgcolor=ft.colors.BLACK,
                padding=20, expand=True,
                content=ft.Column([
                    header,
                    ft.Divider(height=1),
                    kind_row,
                    self.search,
                    self.list_info,
                    self.list_box,
                    self.status,
                    self.diag,
                    bottom,
                ], spacing=10, expand=True),
            )
        ]

        self._load_cache()
        # Sincroniza em background para não travar a interface
        threading.Thread(target=self._safe_sync, daemon=True).start()

    def _safe_sync(self):
        try:
            self._sync()
        except Exception as ex:
            _log(f"picker sync thread erro: {ex}")

    # ----- navegação -----

    def _finish(self, contact):
        if contact:
            self.on_pick_cb(contact)
        else:
            self.on_back_cb()

    # ----- dados -----

    def _set_kind(self, kind):
        self.picker_kind = kind
        self._filter()

    def _load_cache(self):
        try:
            all_contacts = db.list_contacts()
            self.picker_contacts = db.filter_local(
                all_contacts, self.search.value or "", self.picker_kind)
            total = db.count_contacts()
            groups = db.count_groups()
            self.status.value = f"{total} salvo(s) • {groups} grupo(s) em cache."
            self.diag.value = f"DB: {db.DB_PATH}"
            _log(f"picker: cache {total} grupos {groups} "
                 f"filtro {len(self.picker_contacts)}")
            self._render()
        except Exception as ex:
            _log(f"picker cache erro: {ex}")
            self.status.value = f"Erro ao ler cache: {ex}"
            self._render(placeholder="Cache indisponível")
        self._refresh()

    def _filter(self):
        try:
            q = (self.search.value or "").strip().lower()
            all_contacts = db.list_contacts()
            self.picker_contacts = db.filter_local(all_contacts, q, self.picker_kind)
            _log(f"picker filtro: {self.picker_kind} q={q[:20]!r} -> "
                 f"{len(self.picker_contacts)}")
            self._render()
        except Exception as ex:
            _log(f"picker filtro erro: {ex}")
            self._render(placeholder=f"Erro: {ex}")
        self._refresh()

    def _render(self, placeholder: str | None = None):
        cards: list = []
        contatos = 0
        grupos = 0
        for ct in (self.picker_contacts or [])[:300]:
            try:
                if not isinstance(ct, dict) or not ct.get("number"):
                    continue
                name = (ct.get("name") or "").strip() or str(ct.get("number"))
                is_group = bool(ct.get("is_group"))
                if is_group:
                    grupos += 1
                else:
                    contatos += 1
                badge = "Grupo" if is_group else "Contato"
                number = str(ct.get("number"))
                cards.append(ft.Container(
                    margin=ft.margin.only(bottom=4),
                    padding=10,
                    border_radius=8,
                    bgcolor=ft.colors.GREY_800,
                    ink=True,
                    on_click=lambda e, c=dict(ct): self._finish(c),
                    content=ft.Column([
                        ft.Text(f"{name} • {badge}",
                                weight=ft.FontWeight.BOLD,
                                size=14, color=ft.colors.WHITE,
                                max_lines=1,
                                overflow=ft.TextOverflow.ELLIPSIS),
                        ft.Text(number, size=11,
                                color=ft.colors.AMBER_200, max_lines=1),
                    ], spacing=2, tight=True),
                ))
            except Exception as ex:
                _log(f"picker item erro ({ct.get('number')}): {ex}")
                continue
        if not cards:
            msg = placeholder or ("Nada aqui. Toque Sincronizar API para buscar "
                                  "contatos e grupos.")
            cards.append(ft.Container(
                padding=40,
                content=ft.Text(msg, size=13, text_align=ft.TextAlign.CENTER,
                                color=ft.colors.WHITE60)))
        # Recria um ListView NOVO por render (padrão do painel de detalhes,
        # que funciona no Flet 0.24.1) — mutar .controls não renderiza.
        self.list_box.content = ft.ListView(
            cards, spacing=0, padding=4, expand=True)
        try:
            self.list_info.value = (
                f"Mostrando {len(cards)} de {len(self.picker_contacts or [])} "
                f"({contatos} contatos • {grupos} grupos) — filtro: {self.picker_kind}")
        except Exception:
            pass
        _log(f"picker render: {len(cards)} cartões -> ListView novo")

    def _refresh(self):
        for fn in (lambda: self.list_box.update(),
                   lambda: self.list_info.update(),
                   lambda: self.status.update(),
                   lambda: self.page_ref.update()):
            try:
                fn()
            except Exception:
                pass

    def _pick_own(self):
        self.status.value = "Detectando seu número..."
        self._refresh()
        try:
            owner = get_api().fetch_owner_number()
        except Exception:
            owner = None
        if not owner:
            owner = (db.get_settings().get("my_number") or "").strip()
        if not owner:
            self.status.value = "Não achei. Configure em Ajustes > Meu número."
            self._refresh()
            return
        self._finish({"number": owner, "name": "Você", "is_group": False})

    def _sync(self):
        self.status.value = "Sincronizando com a Evolution API..."
        self._refresh()
        try:
            api = get_api()
            contacts = api.find_contacts() or []
            chats = api.find_chats() or []
            groups = api.fetch_groups() or []
            owner = api.fetch_owner_number()
            merged: dict = {}
            for ct in contacts + chats + groups:
                if not isinstance(ct, dict) or not ct.get("number"):
                    continue
                k = ct["number"]
                if k not in merged or (not merged[k]["name"] and ct.get("name")):
                    merged[k] = ct
            if owner and owner not in merged:
                merged[owner] = {"number": owner, "name": "Você",
                                 "is_group": False}
            saved = (db.get_settings().get("my_number") or "").strip()
            saved = db.normalize_number(saved) if saved else ""
            if saved and saved not in merged:
                merged[saved] = {"number": saved, "name": "Meu número",
                                 "is_group": False}
            fresh = list(merged.values())
            if fresh:
                db.replace_contacts(fresh)
            self.picker_contacts = db.filter_local(
                db.list_contacts(), self.search.value or "", self.picker_kind)
            self._render()
            n_groups = sum(1 for c in db.list_contacts() if c.get("is_group"))
            self.status.value = (
                f"{db.count_contacts()} sincronizados ({n_groups} grupos)")
        except Exception as ex:
            _log(f"picker sync erro: {ex}")
            self._render(placeholder=f"Erro na API: {ex}")
            self.status.value = f"Sem conexão — usando cache ({ex})"
        self._refresh()


class MessageDialog(ft.AlertDialog):
    """Diálogo que alterna entre formulário e seletor visual de contatos."""

    def __init__(self, page, on_done, message=None, default_day=None):
        super().__init__(modal=True)
        self.page_ref = page
        self.on_done = on_done
        self.message = message
        self.picker_contacts: list = []
        self.picker_kind: str = "all"
        self.pending_file: str | None = None
        self.saved_media_path: str | None = (message or {}).get("media_path")
        self.saved_media_type: str | None = (message or {}).get("media_type")
        self.file_picker = ft.FilePicker(on_result=self._on_file_picked)
        try:
            page.overlay.append(self.file_picker)
        except Exception:
            pass

        # ---------- Campos do formulário ----------
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

        templates = db.list_templates()
        self.template_dropdown = ft.Dropdown(
            label="Modelo rápido", width=280,
            options=[ft.dropdown.Option(key=str(t["id"]), text=t["title"])
                     for t in templates],
        )

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
            on_click=lambda e: self.file_picker.pick_files(allow_multiple=False),
        )
        remove_btn = ft.IconButton(
            icon=ft.icons.CLOSE, tooltip="Remover anexo",
            on_click=lambda e: self._clear_file(),
        )

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
            [ft.TextButton(h, on_click=lambda e, h=h: self._set_hour(h))
             for h in PRESET_HOURS],
            wrap=True, spacing=4,
        )

        # ---------- Ações ----------
        self.actions = [
            ft.TextButton("Cancelar", on_click=lambda e: self._close()),
            ft.FilledButton("Salvar", icon=ft.icons.SAVE, on_click=lambda e: self._save()),
        ]
        if message:
            self.actions.insert(
                0,
                ft.TextButton(
                    "Excluir", icon=ft.icons.DELETE_OUTLINE,
                    style=ft.ButtonStyle(color=ft.colors.RED_300),
                    on_click=lambda e: self._delete(),
                ),
            )

        # ---------- Formulário (conteúdo inicial) ----------
        self.content = ft.Container(
            width=560,
            content=ft.Column([
                ft.Text("Editar mensagem" if message else "Nova mensagem agendada",
                        size=18, weight=ft.FontWeight.BOLD),
                ft.Column([
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
                ], tight=True, spacing=12, scroll=ft.ScrollMode.AUTO),
            ], tight=False, spacing=14),
        )

    # ==================== Navegação ====================

    def _show_picker(self):
        """Abre o seletor como tela cheia trocando o content.content do app
        (mesmo mecanismo do NavigationRail) — sem AlertDialog aninhado e
        sem page.views, que falham na combinação Flet 0.24.1 + page.add()."""
        _log("abriu seletor")

        anchor = getattr(self.page_ref, "powerzap_content", None)
        if anchor is None:
            _log("seletor: sem anchor powerzap_content na página")
            self.page_ref.open(ft.SnackBar(
                ft.Text("Seletor indisponível neste contexto.")))
            return

        original = anchor.content

        def restore():
            anchor.content = original
            self.page_ref.update()
            self._reopen_dialog()

        def on_pick(contact: dict):
            _log(f"escolheu: {contact.get('name')} {contact.get('number')}")
            self.number_field.value = str(contact.get("number", ""))
            restore()

        def on_back_cb():
            restore()

        # Fecha o diálogo modal antes de trocar a tela
        try:
            self.page_ref.close(self)
        except Exception:
            pass

        picker = ContactPickerView(self.page_ref, on_pick=on_pick, on_back=on_back_cb)
        self._picker_instance = picker
        anchor.content = picker
        self.page_ref.update()

    def _reopen_dialog(self):
        try:
            self.page_ref.open(self)
            self.update()
        except Exception as ex:
            _log(f"reopen dialogo erro: {ex}")
            try:
                self.page_ref.open(self)
            except Exception:
                pass
        try:
            self.text_field.focus()
        except Exception:
            pass

    def _show_form(self):
        self._safe_update()
        try:
            self.text_field.focus()
        except Exception:
            pass

    def _safe_update(self):
        for fn in (lambda: self.update(), lambda: self.page_ref.update()):
            try:
                fn()
            except Exception:
                pass

    # ==================== Formulário ====================

    def _set_hour(self, h):
        self.time_field.value = h
        self._safe_update()

    def _close(self):
        self.page_ref.close(self)

    def _parse_dt(self) -> str | None:
        try:
            dt = datetime.strptime(
                f"{self.date_field.value} {self.time_field.value}",
                "%Y-%m-%d %H:%M",
            )
            return dt.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None

    def _media_display_name(self) -> str | None:
        path = self.pending_file or self.saved_media_path
        if not path:
            return None
        return os.path.basename(path)

    def _refresh_preview(self):
        try:
            path = self.pending_file or self.saved_media_path
            name = os.path.basename(path) if path else "Nenhum arquivo anexado"
            self.file_label.value = name
            is_image = path and os.path.splitext(path)[1].lower() in (
                ".png", ".jpg", ".jpeg", ".gif", ".webp")
            if is_image and os.path.exists(path):
                self.file_preview.src = path
                self.file_preview.visible = True
            else:
                self.file_preview.visible = False
        except Exception:
            pass

    def _on_file_picked(self, e: ft.FilePickerResultEvent):
        if not e.files:
            return
        picked = e.files[0]
        src = picked.path or picked.name
        if picked.path and os.path.exists(picked.path):
            size = os.path.getsize(picked.path)
            if size > db.MAX_MEDIA_BYTES:
                self.page_ref.open(ft.SnackBar(
                    ft.Text("Arquivo maior que 16MB, limite do WhatsApp.")))
                return
            self.pending_file = picked.path
        else:
            self.page_ref.open(ft.SnackBar(
                ft.Text("Não foi possível ler o arquivo local. Tente outro.")))
            return
        self._refresh_preview()
        self._safe_update()

    def _clear_file(self):
        self.pending_file = None
        self.saved_media_path = None
        self.saved_media_type = None
        self._refresh_preview()
        self._safe_update()

    def _apply_template(self):
        tid = self.template_dropdown.value
        if not tid:
            return
        for t in db.list_templates():
            if str(t["id"]) == str(tid):
                self.text_field.value = t["body"]
                self._safe_update()
                break

    def _store_attachment(self) -> tuple[str | None, str | None, str | None]:
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
                ft.SnackBar(ft.Text(
                    "Preencha número, mensagem ou anexo, e data/hora válidas."))
            )
            return
        rec = self.rec_dropdown.value or "none"
        rec_end = (self.rec_end_field.value or "").strip() or None
        if rec_end:
            try:
                datetime.strptime(rec_end, "%Y-%m-%d")
            except ValueError:
                self.page_ref.open(ft.SnackBar(
                    ft.Text("Data de 'Repetir até' inválida. "
                            "Use AAAA-MM-DD ou deixe vazio.")))
                return
        tag_id = int(self.tag_dropdown.value) if self.tag_dropdown.value else None
        try:
            media_path, media_type, mimetype = self._store_attachment()
        except Exception as ex:
            self.page_ref.open(ft.SnackBar(
                ft.Text(f"Falha ao salvar anexo: {ex}")))
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
            "Nova mensagem", icon=ft.icons.ADD,
            on_click=lambda e: self.open_dialog(),
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

    def _legend(self):
        row = []
        for name, color in STATUS_COLORS.items():
            row.append(ft.Container(width=12, height=12, border_radius=6,
                                    bgcolor=color))
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
        if (self.selected_date.month != self.month
                or self.selected_date.year != self.year):
            self.selected_date = date(self.year, self.month, 1)
        self.reload()

    def _go_today(self):
        t = date.today()
        self.year, self.month = t.year, t.month
        self.selected_date = t
        self.reload()

    def _counts_by_day(self) -> dict:
        counts = {}
        for msg in db.list_messages():
            d = msg["scheduled_at"][:10]
            counts.setdefault(d, []).append(msg)
        return counts

    def _month_matrix(self) -> list[list[date]]:
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
            bg = ft.colors.GREEN_700
            fg = ft.colors.WHITE
            day_weight = ft.FontWeight.BOLD
        elif is_today:
            bg = ft.colors.with_opacity(0.35, ft.colors.BLUE_GREY_700)
            fg = ft.colors.WHITE
            day_weight = ft.FontWeight.BOLD
        else:
            bg = ft.colors.with_opacity(0.05, ft.colors.WHITE)
            fg = None
            day_weight = None

        badges = ft.Row([
            ft.Container(width=7, height=7, border_radius=3,
                         bgcolor=STATUS_COLORS.get(m["status"], ft.colors.GREY))
            for m in msgs[:9]
        ], spacing=3, alignment=ft.MainAxisAlignment.CENTER)

        cell = ft.Container(
            bgcolor=bg,
            border_radius=12,
            border=ft.border.all(
                1, ft.colors.with_opacity(0.08, ft.colors.WHITE)),
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
        self.month_label.value = (
            f"{pycal.month_name[self.month].capitalize()} {self.year}")
        days_header = ft.Row([
            ft.Container(
                ft.Text(d, weight=ft.FontWeight.BOLD, size=13,
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
                    content=ft.Text(m["tag_name"], size=11,
                                    color=ft.colors.BLACK),
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
                if (m.get("recurrence") or "none") != "none"
                else ft.Container()
            )
            error_text = (
                ft.Text(f"Erro: {m['error']}", size=11,
                        color=ft.colors.RED_300)
                if m.get("status") == "falhou" and m.get("error")
                else ft.Container()
            )
            action_row: list = []
            if m.get("status") == "falhou":
                action_row.append(ft.TextButton(
                    "Tentar de novo", icon=ft.icons.REFRESH,
                    on_click=lambda e, mid=m["id"]: self._retry(mid)))
            action_row.append(ft.IconButton(
                ft.icons.COPY, icon_size=18,
                tooltip="Duplicar para amanhã",
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
                        ft.Text(m["scheduled_at"][11:16],
                                weight=ft.FontWeight.BOLD, size=16),
                        tag_chip,
                        attach_icon,
                        rec_icon,
                        ft.Container(expand=True),
                        ft.Container(
                            width=10, height=10, border_radius=5,
                            bgcolor=STATUS_COLORS.get(
                                m["status"], ft.colors.GREY),
                        ),
                    ]),
                    ft.Text(body_text, size=13, max_lines=2,
                            overflow=ft.TextOverflow.ELLIPSIS),
                    ft.Text(f"→ {m['number']} • {m['status']}", size=12,
                            color=ft.colors.with_opacity(
                                0.55, ft.colors.WHITE)),
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
