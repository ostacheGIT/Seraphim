import { useState, useCallback, useEffect } from "react";

export type Theme = "dark" | "light";

export function useTheme() {
    const [theme, setTheme] = useState<Theme>(() => {
        return (localStorage.getItem("seraphim-theme") as Theme) || "dark";
    });

    useEffect(() => {
        document.documentElement.setAttribute("data-theme", theme);
        localStorage.setItem("seraphim-theme", theme);
    }, [theme]);

    const toggleTheme = useCallback(() => {
        setTheme((t) => (t === "dark" ? "light" : "dark"));
    }, []);

    return { theme, toggleTheme };
}
