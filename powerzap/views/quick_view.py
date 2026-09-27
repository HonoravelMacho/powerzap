"""CRUD de mensagens rápidas (modelos usados ao agendar)."""
import flet as ft

from powerzap import db


class QuickDialog(ft.AlertDialog):
    """Cria/edita uma mensagem rápida (título + texto)."""

    def __init__(self, page, on_done, template=None):
        super().__init__(modal=True)
        self.page_ref = page
        self.template = template
        self.on_done = on_done

        self.title_field = ft.TextField(
            label="Título (aparece na lista)",
            width=380,
            value=template["title"] if template else "",
        )
        self.body_field = ft.TextField(
            label="Texto da mensagem",
            multiline=True, min_lines=4, max_lines=8, width=380,
            value=template["body"] if template else "",
        )

        self.actions = [
            ft.TextButton("Cancelar", on_click=lambda e: self._close()),
            ft.FilledButton("Salvar", on_click=lambda e: self._save()),
        ]
        self.content = ft.Container(
            width=420,
            content=ft.Column([
                ft.Text("Editar mensagem rápida" if template else "Nova mensagem rápida",
                        size=18, weight=ft.FontWeight.BOLD),
                ft.Text("O texto é copiado para o agendamento e pode ser "
                        "editado antes de salvar.",
                        size=12,
                        color=ft.colors.with_opacity(0.6, ft.colors.WHITE)),
                self.title_field,
                self.body_field,
            ], tight=True, spacing=10),
        )

    def _close(self):
        self.page_ref.close(self)

    def _save(self):
        title = (self.title_field.value or "").strip()
        body = (self.body_field.value or "").strip()
        if not title or not body:
            self.page_ref.open(ft.SnackBar(
                ft.Text("Informe o título e o texto da mensagem.")))
            return
        try:
            if self.template:
                db.update_template(self.template["id"], title, body)
            else:
                db.create_template(title, body)
        except Exception:
            self.page_ref.open(ft.SnackBar(
                ft.Text("Já existe uma mensagem rápida com esse título.")))
            return
        self._close()
        self.on_done()


class QuickView(ft.Column):
    """Lista as mensagens rápidas com criar/editar/excluir."""

    def __init__(self, on_change=None):
        super().__init__(expand=True, spacing=12, scroll=ft.ScrollMode.AUTO)
        self.external_on_change = on_change or (lambda: None)
        self.reload()

    def reload(self):
        header = ft.Row([
            ft.Text("Mensagens rápidas", size=22, weight=ft.FontWeight.BOLD),
            ft.Container(expand=True),
            ft.FilledButton("Nova mensagem rápida", icon=ft.icons.ADD,
                            on_click=self._new),
        ])
        sub = ft.Text(
            "Escolha uma delas na hora de agendar (campo 'Modelo rápido'). "
            "O texto é copiado e pode ser ajustado antes de salvar.",
            size=12, color=ft.colors.with_opacity(0.55, ft.colors.WHITE))
        cards = []
        for t in db.list_templates():
            preview = (t["body"] or "").strip().replace("\n", " ")
            if len(preview) > 90:
                preview = preview[:87] + "..."
            cards.append(
                ft.Container(
                    padding=14,
                    border_radius=12,
                    bgcolor=ft.colors.with_opacity(0.06, ft.colors.WHITE),
                    content=ft.Column([
                        ft.Row([
                            ft.Text(t["title"], weight=ft.FontWeight.W_600),
                            ft.Container(expand=True),
                            ft.IconButton(ft.icons.EDIT_OUTLINED, icon_size=20,
                                          on_click=lambda e, tpl=t: self._edit(tpl)),
                            ft.IconButton(ft.icons.DELETE_OUTLINE, icon_size=20,
                                          style=ft.ButtonStyle(color=ft.colors.RED_300),
                                          on_click=lambda e, tpl=t: self._delete(tpl)),
                        ]),
                        ft.Text(preview, size=13,
                                color=ft.colors.with_opacity(0.65, ft.colors.WHITE)),
                    ], spacing=4),
                )
            )
        self.controls = [header, sub] + (
            cards if cards
            else [ft.Text("Nenhuma mensagem rápida ainda. Crie a primeira.",
                          color=ft.colors.with_opacity(0.5, ft.colors.WHITE))]
        )
        if hasattr(self, "page") and self.page:
            try:
                self.update()
            except AssertionError:
                pass

    def _new(self, e):
        self.page.open(QuickDialog(self.page, on_done=self._changed))

    def _edit(self, template):
        self.page.open(QuickDialog(self.page, on_done=self._changed,
                                   template=template))

    def _delete(self, template):
        def confirm(e):
            db.delete_template(template["id"])
            self.page.close(dlg)
            self._changed()
        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Excluir mensagem rápida?"),
            content=ft.Text(f"'{template['title']}' será removida. "
                            "As mensagens já agendadas não são afetadas."),
            actions=[
                ft.TextButton("Cancelar", on_click=lambda e: self.page.close(dlg)),
                ft.FilledButton("Excluir",
                                style=ft.ButtonStyle(bgcolor=ft.colors.RED_600),
                                on_click=confirm),
            ],
        )
        self.page.open(dlg)

    def _changed(self):
        self.reload()
        self.external_on_change()
