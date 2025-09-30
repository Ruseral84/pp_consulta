# app/bot_matches.py
import argparse
import os
import sys
import signal
import threading
import time
from datetime import date
import pandas as pd
from apscheduler.schedulers.background import BackgroundScheduler

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = BASE_DIR  # tus Excels están en la raíz del proyecto
RESULTS_FILE = os.path.join(DATA_DIR, "RESULTADOS T5.xlsx")

_stop_event = threading.Event()
_scheduler: BackgroundScheduler | None = None


def _read_today_and_delayed_matches() -> tuple[list[dict], list[dict]]:
    """
    Lee RESULTADOS T5.xlsx con el layout fijo:
      Col1: Fecha
      Col2: División
      Col3: Jugador 1
      Col4: Jugador 2
      Col5..Col14: S1-J1, S1-J2, S2-J1, S2-J2, ... S5-J1, S5-J2

    Devuelve (hoy_sin_jugar, retrasados_sin_jugar).
    """
    df = pd.read_excel(RESULTS_FILE)

    # Normaliza fecha (Col1, índice 0)
    df.iloc[:, 0] = pd.to_datetime(df.iloc[:, 0], errors="coerce").dt.normalize()

    # Columnas por posición (según tu formato fijo)
    col_fecha_idx = 0
    col_div_idx = 1 if df.shape[1] > 1 else None
    col_j1_idx = 2 if df.shape[1] > 2 else None
    col_j2_idx = 3 if df.shape[1] > 3 else None

    # Pares de columnas de sets (Sx-J1, Sx-J2)
    set_cols = []
    # Empiezan en la col 5 (índice 4), de dos en dos, hasta 5 sets
    for k in range(5):  # 0..4 => set1..set5
        c1 = 4 + 2 * k
        c2 = 5 + 2 * k
        if df.shape[1] > c2:
            set_cols.append((c1, c2))

    today_ts = pd.Timestamp(date.today())
    hoy, retrasados = [], []

    for _, row in df.iterrows():
        f = row.iloc[col_fecha_idx] if df.shape[1] > col_fecha_idx else None
        if not isinstance(f, pd.Timestamp) or pd.isna(f):
            continue  # sin fecha válida, se ignora

        division = str(row.iloc[col_div_idx]).strip() if col_div_idx is not None else "Desconocida"
        p1 = str(row.iloc[col_j1_idx]).strip() if col_j1_idx is not None else ""
        p2 = str(row.iloc[col_j2_idx]).strip() if col_j2_idx is not None else ""

        # ¿Hay algún punto cargado en los sets?
        any_points = False
        for c1, c2 in set_cols:
            s1 = row.iloc[c1]
            s2 = row.iloc[c2]
            if pd.notna(s1) or pd.notna(s2):
                any_points = True
                break

        # Solo nos interesan los partidos SIN jugar (sin puntos)
        if not any_points:
            partido = {
                "fecha": f.date().isoformat(),
                "division": division if division else "Desconocida",
                "jugador1": p1,
                "jugador2": p2,
            }
            if f == today_ts:
                hoy.append(partido)
            elif f < today_ts:
                retrasados.append(partido)

    return hoy, retrasados


def run_once(verbose: bool = True) -> None:
    try:
        hoy, retrasados = _read_today_and_delayed_matches()
        if verbose:
            # Retrasados
            print("\nPARTIDOS RETRASADOS:")
            if retrasados:
                for m in retrasados:
                    print(f"{m['fecha']} - {m['division']} - {m['jugador1']} vs {m['jugador2']}")
            else:
                print("Ninguno")

            # Hoy (título con fecha de hoy)
            hoy_str = date.today().isoformat()
            print(f"\nPARTIDOS DE HOY — {hoy_str}:")
            if hoy:
                for m in hoy:
                    print(f"{m['fecha']} - {m['division']} - {m['jugador1']} vs {m['jugador2']}")
            else:
                print("Ninguno")
    except Exception as e:
        print(f"Error en run_once: {e}", file=sys.stderr)


def _handle_stop(*_args):
    global _scheduler
    if _scheduler:
        try:
            _scheduler.shutdown(wait=False)
        except Exception:
            pass
    _stop_event.set()


def main():
    parser = argparse.ArgumentParser(description="Bot de partidos (Temporada 5)")
    parser.add_argument("--once", action="store_true", help="Ejecutar una sola vez y salir")
    args = parser.parse_args()

    if args.once:
        run_once(verbose=True)
        return

    global _scheduler
    _scheduler = BackgroundScheduler(timezone="Europe/Madrid")
    # Lunes a jueves a las 09:00
    _scheduler.add_job(run_once, "cron", day_of_week="mon,tue,wed,thu", hour=9, minute=0, id="diario_9")
    _scheduler.start()

    print("Programado L-J a las 09:00 (Europe/Madrid). Ctrl+C o Ctrl+Break para salir.")

    signal.signal(signal.SIGINT, _handle_stop)
    if hasattr(signal, "SIGTERM"):
        try:
            signal.signal(signal.SIGTERM, _handle_stop)
        except Exception:
            pass
    if hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, _handle_stop)
        except Exception:
            pass

    try:
        while not _stop_event.is_set():
            time.sleep(0.2)
    finally:
        _handle_stop()


if __name__ == "__main__":
    main()
