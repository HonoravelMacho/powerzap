import sys

if "--diagnose" in sys.argv or "--diagnostico" in sys.argv:
    from powerzap.main import diagnose
    diagnose()
    sys.exit(0)

from powerzap.main import main
import flet as ft

if __name__ == "__main__":
    ft.app(target=main)
