"""Tests de l'agent desktop (sans LLM ni écran)."""

import pytest

from seraphim.agents.desktop import parse_decision, risky_family
from seraphim.agents.router import route
from seraphim.desktop.computer import Element, Observation, escape_text, keys_to_pywinauto


@pytest.mark.parametrize("spec,expected", [
    ("enter", "{ENTER}"),
    ("ctrl+l", "^l"),
    ("ctrl+shift+t", "^+t"),
    ("alt+f4", "%{F4}"),
    ("win+d", "{VK_LWIN down}d{VK_LWIN up}"),
    ("ctrl+l, enter", "^l{ENTER}"),
    ("ctrl+a; delete", "^a{DELETE}"),
])
def test_keys_to_pywinauto(spec, expected):
    assert keys_to_pywinauto(spec) == expected


@pytest.mark.parametrize("spec", ["", "hyper+x", "ctrl+banane"])
def test_keys_to_pywinauto_invalid(spec):
    with pytest.raises(ValueError):
        keys_to_pywinauto(spec)


def test_escape_text():
    assert escape_text("a+b (x) {y} 50%") == "a{+}b {(}x{)} {{}y{}} 50{%}"
    assert escape_text("l1\nl2\tz") == "l1{ENTER}l2{TAB}z"


def test_parse_decision_plain_and_wrapped():
    d = parse_decision('{"thought": "ok", "action": "Click", "target": 3}')
    assert d["action"] == "click" and d["target"] == 3
    d = parse_decision('Voici :\n```json\n{"thought": "t", "action": "type", "target": "[7]", "text": "salut"}\n```')
    assert d["target"] == 7 and d["text"] == "salut"
    with pytest.raises(ValueError):
        parse_decision("je ne sais pas")


def test_risky_family_blocks_unrequested():
    assert risky_family("Supprimer définitivement", "range mon bureau") == "suppression"
    assert risky_family("Payer maintenant", "regarde le panier") == "paiement"
    assert risky_family("Envoyer", "écris un brouillon de mail") == "envoi"


def test_risky_family_allows_requested():
    assert risky_family("Supprimer", "supprime ce fichier") is None
    assert risky_family("Envoyer", "envoie le mail à Paul") is None
    assert risky_family("Sept", "calcule 7+5") is None


def test_observation_prompt_and_lookup():
    obs = Observation(
        window_title="Calculatrice", window_hwnd=1, windows=["Calculatrice", "Chrome"],
        elements=[Element(1, "Button", "Sept", 10, 20), Element(2, "Edit", "Nom", 5, 5, value="abc")],
    )
    text = obs.to_prompt()
    assert "[1] Button Sept" in text and "[2] Edit Nom = abc" in text and "« Chrome »" in text
    assert obs.element(2).name == "Nom" and obs.element(9) is None and obs.element(None) is None


@pytest.mark.parametrize("query,agent", [
    ("clique sur le bouton Paramètres", "desktop"),
    ("qu'est-ce qu'il y a sur mon écran ?", "desktop"),
    ("analyse mon écran", "desktop"),
    ("remplis le formulaire avec mon nom", "desktop"),
    ("fais défiler la page", "desktop"),
    ("ouvre chrome", "react"),
    ("écris un script python", "coder"),
])
def test_routing(query, agent):
    assert route(query).agent == agent


# ── Vision, propositions, références à l'écran ───────────────────────────────

@pytest.mark.parametrize("query,agent", [
    ("c'est quoi cette erreur ?", "desktop"),
    ("aide-moi avec ce popup", "desktop"),
    ("résume cette page", "desktop"),
    ("qu'est-ce qui s'affiche à l'écran ?", "desktop"),
    ("what's on my screen?", "desktop"),
    ("aide-moi à voir le README de ce projet", "desktop"),
    ("pourquoi ce code plante ?", "desktop"),
    ("écris un mail à Paul", "chat"),
])
def test_routing_screen_references(query, agent):
    decision = route(query)
    if agent == "desktop":
        assert decision.agent == "desktop"
    else:
        assert decision.agent != "desktop"


def test_routing_pasted_content_is_not_screen():
    assert route("c'est quoi cette erreur ?\nTraceback (most recent call last):\n  ...").agent != "desktop"


@pytest.mark.parametrize("text,ok", [
    ("oui", True), ("Oui !", True), ("vas-y", True), ("ok fais-le", True), ("d'accord merci", True),
    ("non", False), ("oui mais plutôt annuler", False), ("ouvre chrome", False),
    ("oui, et ensuite ferme toutes les fenêtres du bureau stp", False),
])
def test_is_confirmation(text, ok):
    from seraphim.desktop.pending import is_confirmation
    assert is_confirmation(text) is ok


def test_pending_roundtrip_and_expiry(monkeypatch):
    from seraphim.desktop import pending

    pending.set_pending("s1", "clique sur Réessayer", "aide-moi")
    assert pending.get_pending("s2") is None
    assert pending.get_pending("s1").task == "clique sur Réessayer"
    assert pending.pop_pending("s1").task == "clique sur Réessayer"
    assert pending.get_pending("s1") is None

    pending.set_pending(None, "x", "y")
    monkeypatch.setattr(pending.time, "time", lambda: pending._pending["_"].created + pending.TTL_S + 1)
    assert pending.get_pending(None) is None


@pytest.mark.parametrize("query,expected", [
    ("clique sur Valider", True),
    ("peux-tu cliquer sur Valider ?", True),
    ("Seraphim, ouvre le fichier README", True),
    ("est-ce que tu peux fermer ce popup", True),
    ("remplis le formulaire avec mon nom", True),
    ("qu'est-ce qu'il y a sur mon écran ?", False),
    ("c'est quoi cette erreur ?", False),
    ("résume cette page", False),
    ("aide-moi avec ce popup", False),
    ("aide-moi, je veux voir le fichier README de ce projet", False),
    ("montre-moi où est le bouton", False),
])
def test_is_order(query, expected):
    from seraphim.agents.desktop import is_order
    assert is_order(query) is expected


@pytest.mark.parametrize("text,answer,action", [
    ("Une erreur réseau.\nACTION: clique sur « Réessayer »", "Une erreur réseau.", "clique sur « Réessayer »"),
    ("Rien à faire.\n**ACTION:** aucune", "Rien à faire.", ""),
    ("Juste une réponse.", "Juste une réponse.", ""),
])
def test_split_action_line(text, answer, action):
    from seraphim.agents.desktop import split_action_line
    assert split_action_line(text) == (answer, action)


def test_parse_decision_coordinates():
    d = parse_decision('{"thought": "t", "action": "click_at", "x": "120", "y": 360.0, "text": "README"}')
    assert d["x"] == 120 and d["y"] == 360 and d["action"] == "click_at"


def test_shot_to_screen_maps_and_clamps():
    from seraphim.desktop.computer import Shot

    s = Shot(b64="", width=1280, height=700, origin=(-8, -8), scale=0.5)
    assert s.to_screen(100, 50) == (192, 92)
    assert s.to_screen(5000, -20) == (-8 + int(1279 / 0.5), -8)


def test_annotate_draws_marks():
    from PIL import Image

    from seraphim.desktop.computer import Element, annotate

    img = Image.new("RGB", (400, 300), "white")
    annotate(img, (100, 100), [Element(1, "Button", "OK", x=200, y=200, w=80, h=30),
                               Element(2, "Texte", "ligne", x=150, y=130)])
    assert img.getpixel((60, 100)) != (255, 255, 255)   # bord gauche du cadre (centre 100,100 ; 80x30)
    assert (255, 0, 170) in {c for _, c in img.getcolors(10000)}


# ── Trouver et activer ce qui est visible ────────────────────────────────────

@pytest.mark.parametrize("query,expected", [
    ("clique sur le bouton Paramètres", ("click", "le bouton Paramètres")),
    ("ouvre README.md", ("open", "README.md")),
    ("peux-tu double-cliquer sur Corbeille ?", ("double", "Corbeille")),
    ("fais un clic droit sur l'icône Chrome", ("right", "l'icône Chrome")),
    ("Seraphim, lance Spotify", ("open", "Spotify")),
    ("ouvre chrome et va sur youtube", None),
    ("clique sur OK puis sur Fermer", None),
    ("c'est quoi cette erreur ?", None),
])
def test_parse_simple_order(query, expected):
    from seraphim.agents.desktop import parse_simple_order
    assert parse_simple_order(query) == expected


@pytest.mark.parametrize("raw,clean", [
    ("le bouton Paramètres", "Paramètres"),
    ("« Réessayer »", "Réessayer"),
    ("le fichier README.md", "README.md"),
    ("sur le lien GitHub", "GitHub"),
    ("la case J'accepte", "J'accepte"),
    ("Chrome", "Chrome"),
])
def test_clean_target(raw, clean):
    from seraphim.desktop.activate import clean_target
    assert clean_target(raw) == clean


def test_match_score_ranks_sensibly():
    from seraphim.desktop.activate import match_score as s

    assert s("Paramètres", "Paramètres") == 100
    assert s("parametres", "Paramètres") == 100                # accents / casse
    assert s("README", "README.md") >= 90
    assert s("Explorateur de fichiers", "Explorateur de fichiers - 1 fenêtre en cours") >= 80
    assert s("Réessayer", "Reessayer") >= 80                   # faute d'OCR
    assert s("OK", "Bloc-notes OKLM") < 80
    assert s("Fichier", "Paramètres") < 80


def test_activation_methods_by_kind():
    from seraphim.desktop.activate import Candidate, _methods

    ui = object()
    assert _methods(Candidate("uia", "a.txt", 0, 0, 100, kind="ListItem", info=ui), "open")[0] == "mouse_double"
    assert _methods(Candidate("uia", "Lien", 0, 0, 100, kind="Hyperlink", info=ui), "open")[0] == "invoke"
    assert _methods(Candidate("uia", "OK", 0, 0, 100, kind="Button", info=ui), "click")[0] == "invoke"
    assert _methods(Candidate("uia", "Case", 0, 0, 100, kind="CheckBox", info=ui), "click")[0] == "toggle"
    assert _methods(Candidate("ocr", "texte", 0, 0, 90), "click") == ["mouse"]
    assert _methods(Candidate("ocr", "texte", 0, 0, 90), "open") == ["mouse_double", "mouse"]


def test_screen_changed():
    from seraphim.desktop.activate import screen_changed

    base = (1, "A", ("A",), bytes([10] * 100))
    assert not screen_changed(base, (1, "A", ("A",), bytes([10] * 99 + [60])))
    assert screen_changed(base, (2, "B", ("A", "B"), bytes([10] * 100)))
    assert screen_changed(base, (1, "A", ("A",), bytes([40] * 100)))


@pytest.mark.parametrize("raw,target,hint", [
    ("le fichier README.md que tu vois dans la fenetre windows eexplorer", "le fichier README.md", "windows eexplorer"),
    ("README.md dans l'explorateur", "README.md", "explorateur"),
    ("l'icône Chrome sur le bureau", "l'icône Chrome", "bureau"),
    ("le lien GitHub à l'écran", "le lien GitHub", ""),
    ("Paramètres", "Paramètres", ""),
])
def test_split_location(raw, target, hint):
    from seraphim.desktop.activate import split_location
    assert split_location(raw) == (target, hint)


@pytest.mark.parametrize("query,agent", [
    ("tu peux ouvrir le fichier README.md que tu vois dans la fenetre windows eexplorer", "desktop"),
    ("ouvre README.md", "desktop"),
    ("clique sur Démarrer", "desktop"),
    ("ouvre chrome", "react"),
    ("ouvre chrome et va sur youtube", "react"),
])
def test_routing_open_visible_items(query, agent):
    assert route(query).agent == agent


@pytest.mark.parametrize("query,expected", [
    ("Vous pouvez ouvrir un fichier rythmique et tu vois dans Windows Explorer.",
     ("open", "un fichier rythmique et tu vois dans Windows Explorer")),
    ("je voudrais que tu ouvres README.md", ("open", "README.md")),
    ("pouvez-vous cliquer sur Renommer ?", ("click", "Renommer")),
    ("ouvre-moi le dossier src", ("open", "le dossier src")),
])
def test_parse_simple_order_dictation(query, expected):
    from seraphim.agents.desktop import parse_simple_order
    assert parse_simple_order(query) == expected


def test_split_location_misheard_relative():
    from seraphim.desktop.activate import clean_target, split_location

    target = "un fichier rythmique et tu vois dans Windows Explorer"
    assert split_location(target) == ("un fichier rythmique", "Windows Explorer")
    assert clean_target(target) == "rythmique"


FILES = [".git", "configs", "scripts", "src", "tests", "voices", ".gitignore", "LICENSE",
         "pyproject.toml", "README.md", "Seraphim.cmd", "seraphim.spec", "uv.lock"]


@pytest.mark.parametrize("heard,expected", [
    ("rythmique", "README.md"),      # README dicté, mal entendu
    ("rid mi", "README.md"),
    ("licence", "LICENSE"),
    ("paille projet", "pyproject.toml"),
    ("banane", ""),                  # rien ne se prononce pareil → pas de proposition
])
def test_guess_misheard(heard, expected):
    from seraphim.desktop.activate import guess_misheard
    assert guess_misheard(heard, FILES) == expected
