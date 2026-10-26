"""Courbe de charge fictive, pour tester l'application sans données réelles."""

import numpy as np
import pandas as pd


def courbe_demo(jours: int = 14, fin: pd.Timestamp | None = None, graine: int = 42) -> pd.DataFrame:
    """Appartement type : talon ~150 W, ballon d'eau chaude la nuit,
    cuisine le soir, lave-linge certains jours en heures pleines."""
    rng = np.random.default_rng(graine)
    fin = (fin or pd.Timestamp.today()).normalize()
    dates = pd.date_range(fin - pd.Timedelta(days=jours), fin, freq="30min", inclusive="right")
    debut = dates - pd.Timedelta(minutes=30)
    heure = debut.hour + debut.minute / 60

    puissance = 150 + rng.normal(0, 15, len(dates))
    puissance += np.where((heure >= 1) & (heure < 3.5), 1800, 0)              # ballon (contact HC)
    puissance += np.where((heure >= 7) & (heure < 8), 600, 0)                 # petit-déjeuner
    puissance += np.where((heure >= 19) & (heure < 21), 900, 0)               # cuisine, TV
    lave_linge = (debut.day_of_week.isin([2, 5])) & (heure >= 18) & (heure < 19.5)
    puissance += np.where(lave_linge, 2000, 0)         # chauffe de l'eau
    return pd.DataFrame({"date": dates, "puissance_w": puissance.clip(min=50).round()})
