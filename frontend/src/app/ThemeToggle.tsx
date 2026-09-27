import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useAppDispatch, useAppState } from "@/lib/AppState";

/**
 * Dark/light switch, stored per user (see lib/theme.ts).
 *
 * Shows the icon of the theme you would GET, not the one you are in — a sun
 * while dark reads as "go light". The aria-label says it in words, because
 * the icon alone is ambiguous to a screen reader.
 */
export function ThemeToggle() {
  const { theme } = useAppState();
  const dispatch = useAppDispatch();
  const target = theme === "dark" ? "light" : "dark";

  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label={`Switch to ${target} mode`}
      title={`Switch to ${target} mode`}
      onClick={() => dispatch({ type: "toggleTheme" })}
    >
      {theme === "dark" ? (
        <Sun className="size-4" aria-hidden />
      ) : (
        <Moon className="size-4" aria-hidden />
      )}
    </Button>
  );
}
