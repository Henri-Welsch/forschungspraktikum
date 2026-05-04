import matplotlib.pyplot as plt
from shapely.geometry import Polygon
import numpy as np


def interpolate_polygons(poly_inner, poly_outer, t=0.5):
    """
    Interpoliert zwischen zwei Polygonen.
    poly_inner: das kleinere Polygon (z. B. RQ100)
    poly_outer: das größere Polygon (z. B. RQ200)
    t: Interpolationsfaktor (0.5 entspricht dem Mittelwert, also RQ150)
    """
    # Extrahiere die Koordinaten der Außenränder
    coords_inner = np.array(poly_inner.exterior.coords)
    coords_outer = np.array(poly_outer.exterior.coords)

    # Entferne den letzten Punkt, wenn er gleich dem ersten ist
    if np.allclose(coords_inner[0], coords_inner[-1]):
        coords_inner = coords_inner[:-1]
    if np.allclose(coords_outer[0], coords_outer[-1]):
        coords_outer = coords_outer[:-1]

    # Bestimme die Anzahl der Stützpunkte, die wir verwenden wollen
    n_inner = len(coords_inner)
    n_outer = len(coords_outer)
    n = max(n_inner, n_outer)

    # Erzeuge Indizes, um beide Arrays auf die gleiche Anzahl von Punkten zu bringen
    idx_inner = np.linspace(0, n_inner, n, endpoint=False, dtype=int)
    idx_outer = np.linspace(0, n_outer, n, endpoint=False, dtype=int)

    coords_inner_interp = coords_inner[idx_inner]
    coords_outer_interp = coords_outer[idx_outer]

    # Interpolation: jeweils t-Anteil der Differenz hinzufügen
    coords_mid = (1 - t) * coords_inner_interp + t * coords_outer_interp

    # Schließe das Polygon, falls es noch nicht geschlossen ist
    coords_mid = np.vstack([coords_mid, coords_mid[0]])

    return Polygon(coords_mid)


if __name__ == "__main__":
    # Definiere beispielhaft zwei Polygone:
    # RQ100: ein kleineres, etwas unregelmäßiges Polygon
    rq100_coords = [(2, 2), (3, 1), (5, 2), (5, 4), (3, 5), (2, 4), (2, 2)]
    rq100_poly = Polygon(rq100_coords)

    # RQ200: ein größeres Polygon
    rq200_coords = [(1, 1), (4, 0), (6, 1), (7, 3), (6, 6), (4, 7), (1, 6), (0, 3), (1, 1)]
    rq200_poly = Polygon(rq200_coords)

    # Berechne das Zwischenpolygon (RQ150) als Mittelwert zwischen RQ100 und RQ200
    rq150_poly = interpolate_polygons(rq100_poly, rq200_poly, t=0.5)

    # Plotten der Polygone
    fig, ax = plt.subplots(figsize=(8, 8))

    # RQ100 in blau
    x, y = rq100_poly.exterior.xy
    ax.plot(x, y, label="RQ100", color="blue", linewidth=2)

    # RQ200 in rot
    x, y = rq200_poly.exterior.xy
    ax.plot(x, y, label="RQ200", color="red", linewidth=2)

    # Interpoliertes Polygon RQ150 in grün gestrichelt
    x, y = rq150_poly.exterior.xy
    ax.plot(x, y, label="RQ150 (Interpoliert)", color="green", linestyle="--", linewidth=2)

    ax.set_title("Interpolation zwischen RQ100 und RQ200")
    ax.legend()
    ax.set_aspect("equal", adjustable="datalim")
    plt.grid(True)
    plt.show()
