import { cn } from "cn";
import { band } from "@/lib/score";

/**
 * Resume strength as a ring, matching the reference library cards.
 *
 * `score === null` means NEVER ANALYSED, which is not the same as zero — a 0%
 * ring would read as a terrible resume rather than an unmeasured one, so that
 * case renders a dash and a neutral track.
 */
export function ScoreRing({
  score,
  size = 56,
  className,
}: {
  score: number | null;
  size?: number;
  className?: string;
}) {
  const stroke = 5;
  const r = (size - stroke) / 2;
  const circumference = 2 * Math.PI * r;
  const pct = score === null ? 0 : Math.max(0, Math.min(100, score));
  const tone = score === null ? null : band(score);

  return (
    <div
      className={cn("relative shrink-0", className)}
      style={{ width: size, height: size }}
      role="img"
      aria-label={score === null ? "Not analysed yet" : `Resume strength ${score} out of 100`}
    >
      <svg width={size} height={size} className="-rotate-90">
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          strokeWidth={stroke}
          className="stroke-surface-3"
        />
        {score !== null && (
          <circle
            cx={size / 2}
            cy={size / 2}
            r={r}
            fill="none"
            strokeWidth={stroke}
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={circumference * (1 - pct / 100)}
            className={cn("transition-[stroke-dashoffset] duration-500", tone?.className)}
            stroke="currentColor"
          />
        )}
      </svg>
      <span
        className={cn(
          "absolute inset-0 grid place-items-center text-xs font-semibold",
          score === null ? "text-content-muted" : tone?.className,
        )}
      >
        {score === null ? "–" : `${score}%`}
      </span>
    </div>
  );
}
