import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import type { Stats } from "@/lib/api";
import { dayTitle, weekday } from "@/lib/format";

type Day = Stats["daily"][number];

/** Reviews per day, as columns (one series, so no legend: the title names it).
 * Following the dataviz rules: bars at most 24px wide with a 4px rounded top
 * and a square base, a hairline baseline, one gridline at the peak with its
 * value, and each bar's numbers on hover and keyboard focus. A table with the
 * same numbers is there for screen readers, so no value depends on hovering. */
export function DailyChart({ daily, endsToday }: { daily: Day[]; endsToday: boolean }) {
  const peak = Math.max(0, ...daily.map((d) => d.reviews));
  const total = daily.reduce((sum, d) => sum + d.reviews, 0);
  const busiest = peak > 0 ? daily.findLast((d) => d.reviews === peak) : undefined;
  const last = daily.at(-1)?.day;

  return (
    <figure className="rounded-xl border border-border bg-card p-4">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h3 className="text-sm font-medium">Reviews per day</h3>
        <p className="text-xs text-muted-foreground">
          {total === 0
            ? `No reviews in the last ${daily.length} days.`
            : `${total} in ${daily.length} days; the busiest was ${dayTitle(busiest!.day)} (${peak}).`}
        </p>
      </figcaption>

      <div className="relative mt-4 h-36">
        {peak > 0 && (
          <div className="absolute inset-x-0 top-0 border-t border-border" aria-hidden="true">
            <span className="absolute -top-2 left-0 bg-card pr-1 text-[10px] tabular-nums text-muted-foreground">
              {peak}
            </span>
          </div>
        )}
        <div className="absolute inset-0 flex items-end justify-around border-b border-border pl-6">
          {daily.map((d) => (
            <Tooltip key={d.day}>
              <TooltipTrigger asChild>
                <button
                  type="button"
                  tabIndex={0}
                  aria-label={`${dayTitle(d.day)}: ${d.reviews} reviews, ${d.findings} findings`}
                  className="group flex h-full w-10 items-end justify-center rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/60"
                >
                  <span
                    className="block w-full max-w-6 rounded-t-[4px] bg-chart-1 transition-[filter] group-hover:brightness-110 group-focus-visible:brightness-110"
                    style={{ height: peak > 0 ? `${(d.reviews / peak) * 100}%` : 0 }}
                  />
                </button>
              </TooltipTrigger>
              <TooltipContent side="top">
                {/* One block: the content is a flex row, which would put the
                    day on the same line. */}
                <div>
                  <span className="font-semibold">
                    {d.reviews} {d.reviews === 1 ? "review" : "reviews"}
                  </span>
                  , {d.findings} {d.findings === 1 ? "finding" : "findings"}
                  <span className="block opacity-80">{dayTitle(d.day)}</span>
                </div>
              </TooltipContent>
            </Tooltip>
          ))}
        </div>
      </div>
      <div className="flex justify-around pl-6 pt-1.5 text-[11px] text-muted-foreground" aria-hidden="true">
        {daily.map((d) => (
          <span key={d.day} className="w-10 text-center">
            {endsToday && d.day === last ? "Today" : weekday(d.day)}
          </span>
        ))}
      </div>

      <table className="sr-only">
        <caption>Reviews and posted findings per day (UTC)</caption>
        <thead>
          <tr>
            <th scope="col">Day</th>
            <th scope="col">Reviews</th>
            <th scope="col">Findings</th>
          </tr>
        </thead>
        <tbody>
          {daily.map((d) => (
            <tr key={d.day}>
              <th scope="row">{dayTitle(d.day)}</th>
              <td>{d.reviews}</td>
              <td>{d.findings}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
