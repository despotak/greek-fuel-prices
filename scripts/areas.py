"""The 52 areas the bulletins report: the national average and the 51 prefectures (νομοί).

Each entry: area_id (stable ASCII slug), Greek name, English name, and the label exactly as the
bulletin prints it (genitive, capitals). Order is the bulletin's own row order.
"""
import csv
import pathlib

AREAS = [
    ("national", "Πανελλαδικός μέσος όρος", "Greece (national average)", "Πανελλαδικός σταθμισμένος Μ.Ο."),
    ("attiki", "Αττική", "Attica", "ΑΤΤΙΚΗΣ"),
    ("aitoloakarnania", "Αιτωλοακαρνανία", "Aetolia-Acarnania", "ΑΙΤΩΛΙΑΣ ΚΑΙ ΑΚΑΡΝΑΝΙΑΣ"),
    ("argolida", "Αργολίδα", "Argolis", "ΑΡΓΟΛΙΔΟΣ"),
    ("arkadia", "Αρκαδία", "Arcadia", "ΑΡΚΑΔΙΑΣ"),
    ("arta", "Άρτα", "Arta", "ΑΡΤΗΣ"),
    ("achaia", "Αχαΐα", "Achaea", "ΑΧΑΪΑΣ"),
    ("viotia", "Βοιωτία", "Boeotia", "ΒΟΙΩΤΙΑΣ"),
    ("grevena", "Γρεβενά", "Grevena", "ΓΡΕΒΕΝΩΝ"),
    ("drama", "Δράμα", "Drama", "ΔΡΑΜΑΣ"),
    ("dodekanisa", "Δωδεκάνησα", "Dodecanese", "ΔΩΔΕΚΑΝΗΣΟΥ"),
    ("evros", "Έβρος", "Evros", "ΕΒΡΟΥ"),
    ("evvoia", "Εύβοια", "Euboea", "ΕΥΒΟΙΑΣ"),
    ("evrytania", "Ευρυτανία", "Evrytania", "ΕΥΡΥΤΑΝΙΑΣ"),
    ("zakynthos", "Ζάκυνθος", "Zakynthos", "ΖΑΚΥΝΘΟΥ"),
    ("ileia", "Ηλεία", "Elis", "ΗΛΕΙΑΣ"),
    ("imathia", "Ημαθία", "Imathia", "ΗΜΑΘΙΑΣ"),
    ("irakleio", "Ηράκλειο", "Heraklion", "ΗΡΑΚΛΕΙΟΥ"),
    ("thesprotia", "Θεσπρωτία", "Thesprotia", "ΘΕΣΠΡΩΤΙΑΣ"),
    ("thessaloniki", "Θεσσαλονίκη", "Thessaloniki", "ΘΕΣΣΑΛΟΝΙΚΗΣ"),
    ("ioannina", "Ιωάννινα", "Ioannina", "ΙΩΑΝΝΙΝΩΝ"),
    ("kavala", "Καβάλα", "Kavala", "ΚΑΒΑΛΑΣ"),
    ("karditsa", "Καρδίτσα", "Karditsa", "ΚΑΡΔΙΤΣΗΣ"),
    ("kastoria", "Καστοριά", "Kastoria", "ΚΑΣΤΟΡΙΑΣ"),
    ("kerkyra", "Κέρκυρα", "Corfu", "ΚΕΡΚΥΡΑΣ"),
    ("kefallinia", "Κεφαλληνία", "Kefalonia", "ΚΕΦΑΛΛΗΝΙΑΣ"),
    ("kilkis", "Κιλκίς", "Kilkis", "ΚΙΛΚΙΣ"),
    ("kozani", "Κοζάνη", "Kozani", "ΚΟΖΑΝΗΣ"),
    ("korinthia", "Κορινθία", "Corinthia", "ΚΟΡΙΝΘΙΑΣ"),
    ("kyklades", "Κυκλάδες", "Cyclades", "ΚΥΚΛΑΔΩΝ"),
    ("lakonia", "Λακωνία", "Laconia", "ΛΑΚΩΝΙΑΣ"),
    ("larisa", "Λάρισα", "Larissa", "ΛΑΡΙΣΗΣ"),
    ("lasithi", "Λασίθι", "Lasithi", "ΛΑΣΙΘΙΟΥ"),
    ("lesvos", "Λέσβος", "Lesbos", "ΛΕΣΒΟΥ"),
    ("lefkada", "Λευκάδα", "Lefkada", "ΛΕΥΚΑΔΟΣ"),
    ("magnisia", "Μαγνησία", "Magnesia", "ΜΑΓΝΗΣΙΑΣ"),
    ("messinia", "Μεσσηνία", "Messenia", "ΜΕΣΣΗΝΙΑΣ"),
    ("xanthi", "Ξάνθη", "Xanthi", "ΞΑΝΘΗΣ"),
    ("pella", "Πέλλα", "Pella", "ΠΕΛΛΗΣ"),
    ("pieria", "Πιερία", "Pieria", "ΠΙΕΡΙΑΣ"),
    ("preveza", "Πρέβεζα", "Preveza", "ΠΡΕΒΕΖΗΣ"),
    ("rethymno", "Ρέθυμνο", "Rethymno", "ΡΕΘΥΜΝΗΣ"),
    ("rodopi", "Ροδόπη", "Rhodope", "ΡΟΔΟΠΗΣ"),
    ("samos", "Σάμος", "Samos", "ΣΑΜΟΥ"),
    ("serres", "Σέρρες", "Serres", "ΣΕΡΡΩΝ"),
    ("trikala", "Τρίκαλα", "Trikala", "ΤΡΙΚΑΛΩΝ"),
    ("fthiotida", "Φθιώτιδα", "Phthiotis", "ΦΘΙΩΤΙΔΟΣ"),
    ("florina", "Φλώρινα", "Florina", "ΦΛΩΡΙΝΗΣ"),
    ("fokida", "Φωκίδα", "Phocis", "ΦΩΚΙΔΟΣ"),
    ("chalkidiki", "Χαλκιδική", "Chalkidiki", "ΧΑΛΚΙΔΙΚΗΣ"),
    ("chania", "Χανιά", "Chania", "ΧΑΝΙΩΝ"),
    ("chios", "Χίος", "Chios", "ΧΙΟΥ"),
]
BY_LABEL = {label: area_id for area_id, _, _, label in AREAS}
NAME_EL = {area_id: el for area_id, el, _, _ in AREAS}
FUELS = ["unleaded95", "unleaded100", "super", "diesel", "autogas", "heating_oil"]


def write_csv(path):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["area_id", "name_el", "name_en", "bulletin_label"])
        w.writerows(AREAS)


if __name__ == "__main__":
    import sys
    write_csv(sys.argv[1])
