"""Source resolver ABC + dataclass ResolvedSkill."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


@dataclass(slots=True)
class ResolvedSkill:
    """Un skill trouvé dans une source externe, prêt à être importé.

    Léger — n'inclut pas le corps complet du SKILL.md. L'importeur
    lit le fichier depuis *path* lors de l'installation effective.
    """

    name: str
    source: str
    path: Path
    category: str
    description: str
    commit: str
    sidecar_data: Dict[str, Any] = field(default_factory=dict)


class SourceResolver(ABC):
    """Base abstraite pour un résolveur de source de skills.

    Les implémentations clonent ou mettent à jour un dépôt dans un
    répertoire de cache, parcourent le cache pour trouver les SKILL.md,
    et retournent des objets ResolvedSkill que l'importeur peut installer.
    """

    name: str = ""

    @abstractmethod
    def cache_dir(self) -> Path:
        """Où cette source clone son dépôt."""

    @abstractmethod
    def sync(self) -> None:
        """Clone ou met à jour le dépôt dans le cache."""

    @abstractmethod
    def list_skills(self) -> List[ResolvedSkill]:
        """Parcourt le répertoire de cache et retourne tous les skills trouvables."""

    def resolve(self, query: str) -> List[ResolvedSkill]:
        """Filtre list_skills() par nom (correspondance de sous-chaîne).

        Un *query* vide retourne tous les skills.
        """
        all_skills = self.list_skills()
        if not query:
            return all_skills
        q = query.lower()
        return [s for s in all_skills if q in s.name.lower()]

    def filter_by_category(self, category: str) -> List[ResolvedSkill]:
        """Retourne les skills dont la catégorie correspond exactement à *category*."""
        return [s for s in self.list_skills() if s.category == category]


__all__ = ["ResolvedSkill", "SourceResolver"]
