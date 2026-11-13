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
