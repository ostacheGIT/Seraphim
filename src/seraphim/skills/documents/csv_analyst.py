"""CSVAnalystSkill — load a CSV/TSV and return analysis, stats, or filtered rows."""

from __future__ import annotations

import asyncio
import csv
import io
from pathlib import Path

from seraphim.skills.base import BaseSkill, SkillResult

_MAX_OUTPUT = 8_000
_MAX_ROWS_DISPLAY = 20


class CSVAnalystSkill(BaseSkill):
    name = "csv_analyst"
    description = (
        "Load a CSV or TSV file and return an overview, statistics, or filtered rows. "
        "Use when the user provides a CSV path and asks to analyze, describe, query, or compare data. "
        "Actions: overview (default), head, stats, filter, query."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the CSV or TSV file",
            },
            "action": {
                "type": "string",
                "description": (
                    "'overview': columns, types, row count, sample rows (default). "
                    "'head': first N rows. "
                    "'stats': descriptive statistics (numeric columns). "
                    "'filter': rows where column contains value. "
                    "'query': pandas query expression."
                ),
                "enum": ["overview", "head", "stats", "filter", "query"],
                "default": "overview",
            },
            "n": {
                "type": "integer",
                "description": "Number of rows to return for 'head' or 'filter' (default: 10)",
                "default": 10,
            },
            "column": {
                "type": "string",
                "description": "Column name for 'filter' or column-specific 'stats'",
                "default": "",
            },
            "value": {
                "type": "string",
                "description": "Value substring to match for 'filter'",
                "default": "",
            },
            "query": {
                "type": "string",
                "description": "Pandas query expression for 'query' action, e.g. \"age > 30 and city == 'Paris'\"",
                "default": "",
            },
        },
        "required": ["path"],
    }

    async def run(
        self,
        path: str,
        action: str = "overview",
        n: int = 10,
        column: str = "",
        value: str = "",
        query: str = "",
        **kwargs,
    ) -> SkillResult:
        p = Path(path).expanduser()
        if not p.exists():
            return SkillResult(success=False, output="", error=f"Fichier introuvable : {path}")
        if p.suffix.lower() not in (".csv", ".tsv"):
            return SkillResult(
                success=False,
                output="",
                error=f"Format non supporté : {p.suffix}. Attendu : .csv ou .tsv",
            )

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None, self._analyze, str(p), action, n, column, value, query
        )

    # ── Analysis dispatcher ───────────────────────────────────────────────────

    def _analyze(
        self, path: str, action: str, n: int, column: str, value: str, query_expr: str
    ) -> SkillResult:
        try:
            import pandas as pd
            return self._with_pandas(pd, path, action, n, column, value, query_expr)
        except ImportError:
            pass
        except Exception as e:
            return SkillResult(success=False, output="", error=f"pandas erreur : {e}")

        return self._with_stdlib(path, action, n, column, value)

    # ── pandas backend (rich analysis) ───────────────────────────────────────

    def _with_pandas(self, pd, path, action, n, column, value, query_expr):
        sep = "\t" if path.endswith(".tsv") else ","
        df = pd.read_csv(path, sep=sep, encoding="utf-8", encoding_errors="replace")
        rows, cols = df.shape
        name = Path(path).name

        if action == "overview":
            buf = io.StringIO()
            buf.write(f"**{name}** — {rows:,} lignes × {cols} colonnes\n\n")
            buf.write("**Colonnes :**\n")
            for col, dtype in df.dtypes.items():
                nulls = int(df[col].isna().sum())
                null_info = f" · {nulls} nulls ({nulls*100//rows}%)" if nulls else ""
                sample = df[col].dropna().iloc[0] if not df[col].dropna().empty else "—"
                buf.write(f"  - `{col}` : {dtype}{null_info} · ex: `{sample}`\n")
            buf.write(f"\n**Aperçu — 5 premières lignes :**\n```\n{df.head(5).to_string(index=False)}\n```\n")
            # Numeric summary
            num_cols = df.select_dtypes(include="number").columns.tolist()
            if num_cols:
                buf.write(f"\n**Statistiques numériques :**\n```\n{df[num_cols].describe().round(3).to_string()}\n```\n")
            return SkillResult(success=True, output=buf.getvalue()[:_MAX_OUTPUT])

        elif action == "head":
            out = f"**{name}** — {n} premières lignes :\n```\n{df.head(n).to_string(index=False)}\n```"
            return SkillResult(success=True, output=out[:_MAX_OUTPUT])

        elif action == "stats":
            if column:
                if column not in df.columns:
                    return SkillResult(success=False, output="", error=f"Colonne `{column}` introuvable")
                desc = df[column].describe().round(4).to_string()
                out = f"**Stats `{column}` :**\n```\n{desc}\n```"
            else:
                desc = df.describe(include="all").round(4).to_string()
                out = f"**Stats globales — {name} :**\n```\n{desc}\n```"
            return SkillResult(success=True, output=out[:_MAX_OUTPUT])

        elif action == "filter":
            if not column:
                return SkillResult(success=False, output="", error="Paramètre 'column' requis pour l'action filter")
            if column not in df.columns:
                return SkillResult(success=False, output="", error=f"Colonne `{column}` introuvable. Colonnes : {list(df.columns)}")
            mask = df[column].astype(str).str.contains(value, case=False, na=False)
            filtered = df[mask]
            out = (
                f"**Filtre** `{column}` contient `{value}` — {len(filtered):,}/{rows:,} lignes :\n"
                f"```\n{filtered.head(n).to_string(index=False)}\n```"
            )
            return SkillResult(success=True, output=out[:_MAX_OUTPUT])

        elif action == "query":
            if not query_expr:
                return SkillResult(success=False, output="", error="Paramètre 'query' requis")
            try:
                result_df = df.query(query_expr)
                out = (
                    f"**Query** `{query_expr}` — {len(result_df):,}/{rows:,} lignes :\n"
                    f"```\n{result_df.head(n).to_string(index=False)}\n```"
                )
                return SkillResult(success=True, output=out[:_MAX_OUTPUT])
            except Exception as e:
                return SkillResult(success=False, output="", error=f"Expression invalide : {e}")

        return SkillResult(success=False, output="", error=f"Action inconnue : {action}")

    # ── stdlib fallback (no pandas) ───────────────────────────────────────────

    def _with_stdlib(self, path, action, n, column, value):
        sep = "\t" if path.endswith(".tsv") else ","
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            reader = csv.DictReader(f, delimiter=sep)
            rows_data = list(reader)

        if not rows_data:
            return SkillResult(success=False, output="", error="CSV vide ou sans en-têtes")

        cols = list(rows_data[0].keys())
        name = Path(path).name
        total = len(rows_data)
        buf = io.StringIO()

        if action in ("overview", "head"):
            limit = n if action == "head" else 5
            buf.write(f"**{name}** — {total:,} lignes × {len(cols)} colonnes\n\n")
            buf.write(f"**Colonnes :** {', '.join(f'`{c}`' for c in cols)}\n\n")
            buf.write(f"**Premières {limit} lignes :**\n```\n")
            # Simple table
            buf.write(" | ".join(cols) + "\n")
            buf.write("-|-".join(["-" * min(len(c), 12) for c in cols]) + "\n")
            for row in rows_data[:limit]:
                buf.write(" | ".join(str(row.get(c, ""))[:20] for c in cols) + "\n")
            buf.write("```\n")
            buf.write("\n*(pandas non disponible — installe-le pour des stats avancées)*\n")
            return SkillResult(success=True, output=buf.getvalue()[:_MAX_OUTPUT])

        elif action == "filter":
            if not column:
                return SkillResult(success=False, output="", error="Paramètre 'column' requis")
            filtered = [r for r in rows_data if value.lower() in str(r.get(column, "")).lower()]
            buf.write(f"**Filtre** `{column}` contient `{value}` — {len(filtered)}/{total} lignes :\n```\n")
            for row in filtered[:n]:
                buf.write(" | ".join(f"{k}: {v}" for k, v in row.items()) + "\n")
            buf.write("```\n")
            return SkillResult(success=True, output=buf.getvalue()[:_MAX_OUTPUT])

        elif action == "stats":
            if not column:
                return SkillResult(success=False, output="", error="'column' requis sans pandas")
            nums = []
            for r in rows_data:
                try:
                    nums.append(float(r.get(column, "")))
                except (ValueError, TypeError):
                    pass
            if not nums:
                return SkillResult(success=False, output="", error=f"Colonne `{column}` non numérique")
            s = sum(nums)
            avg = s / len(nums)
            out = (
                f"**Stats `{column}`** — {len(nums)} valeurs\n"
                f"min={min(nums):.4g}  max={max(nums):.4g}  "
                f"avg={avg:.4g}  sum={s:.4g}"
            )
            return SkillResult(success=True, output=out)

        return SkillResult(
            success=False, output="",
            error=f"Action '{action}' nécessite pandas. Installe : pip install pandas",
        )
