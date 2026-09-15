"""Interface principal do PowerZap (Flet)."""
import flet as ft

from powerzap import crashlog

crashlog.install()

from powerzap import db
from powerzap.views.calendar_view import CalendarView
from powerzap.views.tags_view import TagsView
from powerzap.views.connect_view import ConnectView
from powerzap.views.settings_view import SettingsView


def main(page: ft.Page):
    db.init_db()

    page.title = "PowerZap"
    page.theme_mode = ft.ThemeMode.DARK
    page.theme = ft.Theme(color_scheme_seed=ft.colors.GREEN_500)
    page.window_width = 1100
    page.window_height = 720
    page.window_min_width = 900
    page.window_min_height = 600
    page.padding = 0

    content = ft.Container(expand=True)

    calendar_btn = ft.NavigationRailDestination(
        icon=ft.icons.CALENDAR_MONTH_OUTLINED,
        selected_icon=ft.icons.CALENDAR_MONTH,
        label="Calendário",
    )
    connect_btn = ft.NavigationRailDestination(
        icon=ft.icons.QR_CODE_2_OUTLINED,
        selected_icon=ft.icons.QR_CODE_2,
        label="Conexão",
    )
    tags_btn = ft.NavigationRailDestination(
        icon=ft.icons.LABEL_OUTLINED, selected_icon=ft.icons.LABEL, label="Etiquetas"
    )
    settings_btn = ft.NavigationRailDestination(
        icon=ft.icons.SETTINGS_OUTLINED, selected_icon=ft.icons.SETTINGS, label="Ajustes"
    )

    rail = ft.NavigationRail(
        selected_index=0,
        destinations=[calendar_btn, connect_btn, tags_btn, settings_btn],
        on_change=lambda e: switch(e.control.selected_index),
        label_type=ft.NavigationRailLabelType.ALL,
        min_width=90,
        min_extended_width=180,
        bgcolor=ft.colors.SURFACE_VARIANT,
    )

    views = {}

    def refresh():
        idx = rail.selected_index
        if idx in views and hasattr(views[idx], "reload"):
            views[idx].reload()

    def switch(idx: int):
        rail.selected_index = idx
        if idx == 0:
            views.setdefault(0, CalendarView(page, on_change=refresh))
        elif idx == 1:
            views.setdefault(1, ConnectView(page))
        elif idx == 2:
            views[2] = TagsView(on_change=refresh)
        else:
            views.setdefault(3, SettingsView(page))
        content.content = views[idx]
        content.update()
        view_obj = views[idx]
        if hasattr(view_obj, "reload"):
            view_obj.reload()

    page.add(ft.Row([rail, content], spacing=0, expand=True))
    switch(0)


def diagnose():
    """Modo diagnóstico via terminal para o seletor visual."""
    db.init_db()
    total = db.count_contacts()
    groups = db.count_groups()
    contacts = db.list_contacts()
    s = db.get_settings()
    print(f"PowerZap diagnóstico")
    print(f"  DB: {db.DB_PATH}")
    print(f"  Total contatos no cache: {total} | grupos: {groups}")
    print(f"  Settings: url={s.get('evolution_url')} instance={s.get('instance')} my_number={s.get('my_number')}")
    print(f"  Amostra (3):")
    for c in contacts[:3]:
        print(f"    - {c['number']} | {c['name']!r} | grupo={bool(c['is_group'])}")
    try:
        from powerzap.views.connect_view import get_api
        api = get_api()
        print(f"  Evolution: {api.url} instance={api.instance}")
        try:
            st = api.connection_state()
            print(f"  connectionState: {str(st)[:500]}")
        except Exception as ex:
            print(f"  connectionState erro: {ex}")
        try:
            print(f"  find_contacts: {len(api.find_contacts())}")
        except Exception as ex:
            print(f"  find_contacts erro: {ex}")
        try:
            print(f"  find_chats: {len(api.find_chats())}")
        except Exception as ex:
            print(f"  find_chats erro: {ex}")
        try:
            print(f"  fetch_groups: {len(api.fetch_groups())}")
        except Exception as ex:
            print(f"  fetch_groups erro: {ex}")
        try:
            print(f"  owner: {api.fetch_owner_number()}")
        except Exception as ex:
            print(f"  owner erro: {ex}")
    except Exception as ex:
        print(f"  Erro diagnóstico API: {ex}")

    # Teste de render do seletor (sem UI)
    try:
        from powerzap.views.calendar_view import MessageDialog
        class FakePage:
            overlay = []
            def update(self): pass
            def open(self, c): pass
            def close(self, c): pass
        dlg = MessageDialog(FakePage(), on_done=lambda: None)
        dlg._load_contacts()
        print(f"  Seletor render: {len(dlg.picker_contacts)} filtrados | {len(dlg.contact_list.controls)} controles | status={dlg.picker_status.value!r}")
        print("  OK: seletor renderizou sem erro")
    except Exception as ex:
        import traceback
        print(f"  Seletor erro: {ex}")
        traceback.print_exc()


def app():
    import sys
    if "--diagnose" in sys.argv or "--diagnostico" in sys.argv:
        diagnose()
        return
    ft.app(target=main)


if __name__ == "__main__":
    app()
