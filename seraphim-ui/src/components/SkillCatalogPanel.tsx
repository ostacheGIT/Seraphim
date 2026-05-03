import { useState, useEffect, useRef } from "react";
import { Download, RefreshCw, Search } from "lucide-react";
import {
    CatalogSkill,
    searchSkillCatalog,
    installSkill,
    buildSkillCatalog,
    fetchInstalledSkills,
    fetchNativeSkills,
} from "../hooks/useSeraphimBackend";

const PAGE = 200;

interface Props {
    onInstalled: () => void;
}

export default function SkillCatalogPanel({ onInstalled }: Props) {
    const [query, setQuery] = useState("");
    const [results, setResults] = useState<CatalogSkill[]>([]);
    const [offset, setOffset] = useState(0);
    const [hasMore, setHasMore] = useState(false);
    const [catalogSize, setCatalogSize] = useState(0);
    const [installedNames, setInstalledNames] = useState<Set<string>>(new Set());
    const [installedSkills, setInstalledSkills] = useState<CatalogSkill[]>([]);
    const [nativeSkills, setNativeSkills] = useState<CatalogSkill[]>([]);
    const [filter, setFilter] = useState<"all" | "installed" | "native">("all");
    const [installing, setInstalling] = useState<string | null>(null);
    const [building, setBuilding] = useState(false);
    const [status, setStatus] = useState("");
    const debounce = useRef<ReturnType<typeof setTimeout> | null>(null);

    const load = async (q: string, off: number, replace: boolean) => {
        const { skills, catalog_size } = await searchSkillCatalog(q, PAGE, off);
        setCatalogSize(catalog_size);
        setResults((prev) => replace ? skills : [...prev, ...skills]);
        setOffset(off + skills.length);
        setHasMore(skills.length === PAGE);
    };

    const loadInstalled = async () => {
        const skills = await fetchInstalledSkills();
        const names = new Set(skills.map((s) => s.name));
        setInstalledNames(names);
        setInstalledSkills(skills.map((s) => ({
            name: s.name,
            slug: s.id.replace(/^skill:/, ""),
            description: s.description,
            source: s.source,
            category: "installed",
        })));
    };

    const loadNative = async () => {
        const skills = await fetchNativeSkills();
        setNativeSkills(skills.map((s) => ({
            name: s.name,
            slug: s.id.replace(/^skill:/, ""),
            description: s.description,
            source: "native",
            category: "builtin",
        })));
    };

    useEffect(() => {
        loadInstalled();
        loadNative();
        load("", 0, true);
    }, []);

    const handleSearch = (q: string) => {
        setQuery(q);
        if (debounce.current) clearTimeout(debounce.current);
        if (filter === "all") {
            debounce.current = setTimeout(() => load(q, 0, true), 280);
        }
    };

    const handleFilterChange = (f: "all" | "installed" | "native") => {
        setFilter(f);
        setQuery("");
        if (f === "installed") loadInstalled();
        if (f === "native") loadNative();
    };

    const handleInstall = async (skill: CatalogSkill) => {
        setInstalling(skill.slug);
        setStatus("");
        try {
            const res = await installSkill(skill.slug, skill.source);
            if (res.skipped) {
                setStatus(`${skill.name} déjà installé`);
            } else if (res.success) {
                setStatus(`✓ ${skill.name} installé`);
                setInstalledNames((prev) => new Set([...prev, skill.slug, skill.name]));
                await loadInstalled();
                onInstalled();
            } else {
                setStatus(`✗ Échec : ${res.warnings[0] ?? "erreur"}`);
            }
        } catch (e: unknown) {
            setStatus(`✗ ${e instanceof Error ? e.message : "erreur"}`);
        } finally {
            setInstalling(null);
        }
    };

    const handleBuild = async () => {
        setBuilding(true);
        setStatus("Construction du catalogue...");
        try {
            const count = await buildSkillCatalog();
            setStatus(`Catalogue mis à jour — ${count} skills indexés`);
            await load(query, 0, true);
        } catch {
            setStatus("✗ Échec de la mise à jour");
        } finally {
            setBuilding(false);
        }
    };

    const isInstalled = (skill: CatalogSkill) =>
        installedNames.has(skill.slug) || installedNames.has(skill.name);

    const filterByQuery = (skills: CatalogSkill[]) =>
        !query ? skills : skills.filter((s) =>
            s.name.toLowerCase().includes(query.toLowerCase()) ||
            s.description.toLowerCase().includes(query.toLowerCase()));

    const visibleSkills =
        filter === "installed" ? filterByQuery(installedSkills) :
        filter === "native"    ? filterByQuery(nativeSkills) :
        results;

    const renderCard = (skill: CatalogSkill) => {
        const done = filter === "installed" || isInstalled(skill);
        const busy = installing === skill.slug;
        return (
            <div key={`${skill.source}/${skill.slug}`} className="skill-card">
                <div className="skill-card-header">
                    <span className="skill-card-name">{skill.name || skill.slug}</span>
                    <span className={`skill-badge skill-badge-${skill.source}`}>
                        {skill.source}
                    </span>
                </div>
                {skill.description && (
                    <p className="skill-card-desc">
                        {skill.description.slice(0, 100)}
                        {skill.description.length > 100 ? "…" : ""}
                    </p>
                )}
                <div className="skill-card-footer">
                    <span className="skill-card-category">{skill.category}</span>
                    {filter !== "installed" && filter !== "native" && (
                        <button
                            className={`skill-install-btn ${done ? "installed" : ""}`}
                            onClick={() => !done && handleInstall(skill)}
                            disabled={busy || done}
                            aria-label={done ? "Déjà installé" : "Installer"}
                        >
                            <Download size={11} />
                            {busy ? "…" : done ? "Installé" : "Installer"}
                        </button>
                    )}
                </div>
            </div>
        );
    };

    return (
        <div className="catalog-panel">
            <div className="catalog-toolbar">
                <div className="catalog-search-wrap">
                    <Search size={12} className="catalog-search-icon" />
                    <input
                        className="catalog-search"
                        placeholder={
                            filter === "installed" ? `Filtrer ${installedSkills.length} installés…`
                            : filter === "native"  ? `Filtrer ${nativeSkills.length} skills natifs…`
                            : `Rechercher dans ${catalogSize} skills…`
                        }
                        value={query}
                        onChange={(e) => handleSearch(e.target.value)}
                    />
                </div>
                {filter === "all" && (
                    <button
                        className="catalog-build-btn"
                        onClick={handleBuild}
                        disabled={building}
                        aria-label="Mettre à jour le catalogue"
                        title="Reconstruire l'index"
                    >
                        <RefreshCw size={12} className={building ? "spin" : ""} />
                    </button>
                )}
            </div>

            <div className="catalog-filter-bar">
                <button
                    className={`catalog-filter-btn ${filter === "all" ? "active" : ""}`}
                    onClick={() => handleFilterChange("all")}
                >
                    Tous
                </button>
                <button
                    className={`catalog-filter-btn ${filter === "installed" ? "active" : ""}`}
                    onClick={() => handleFilterChange("installed")}
                >
                    Installés{installedSkills.length > 0 ? ` (${installedSkills.length})` : ""}
                </button>
                <button
                    className={`catalog-filter-btn ${filter === "native" ? "active" : ""}`}
                    onClick={() => handleFilterChange("native")}
                >
                    Natifs{nativeSkills.length > 0 ? ` (${nativeSkills.length})` : ""}
                </button>
            </div>

            {status && <div className="catalog-status">{status}</div>}

            <div className="catalog-results">
                {visibleSkills.length === 0 && !building && (
                    <p className="empty-hint">
                        {filter === "installed" ? "Aucun skill installé"
                            : filter === "native" ? "Aucun skill natif trouvé"
                            : catalogSize === 0 ? "Catalogue vide — cliquez ↻ pour indexer"
                            : "Aucun résultat"}
                    </p>
                )}
                {visibleSkills.map(renderCard)}
                {filter === "all" && hasMore && (
                    <button
                        className="catalog-load-more"
                        onClick={() => load(query, offset, false)}
                    >
                        Voir plus ({catalogSize - offset} restants)
                    </button>
                )}
            </div>
        </div>
    );
}
